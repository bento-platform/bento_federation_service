import itertools
import json
import tornado.gen

from bento_lib.search.queries import Query
from datetime import datetime
from tornado.httpclient import AsyncHTTPClient
from tornado.queues import Queue

from typing import Dict, List, Optional, Set, Tuple

from bento_federation_service.constants import CHORD_URL, SERVICE_NAME, WORKERS
from bento_federation_service.utils import peer_fetch, iterable_to_queue
from .constants import DATASET_SEARCH_HEADERS


__all__ = [
    "run_search_on_dataset",
]


FieldSpec = List[str]
DataTypeAndField = Tuple[str, FieldSpec]
DictOfDataTypesAndFields = Dict[str, FieldSpec]
LinkedFieldSetList = List[DictOfDataTypesAndFields]


def _linked_fields_to_join_query_fragment(field_1: DataTypeAndField, field_2: DataTypeAndField) -> Query:
    return ["#eq", ["#resolve", field_1[0], "[item]", *field_1[1]], ["#resolve", field_2[0], "[item]", *field_2[1]]]


def _linked_field_set_to_join_query_rec(pairs: tuple) -> Query:
    if len(pairs) == 1:
        return _linked_fields_to_join_query_fragment(*pairs[0])

    return ["#and",
            _linked_fields_to_join_query_fragment(*pairs[0]),
            _linked_field_set_to_join_query_rec(pairs[1:])]


def _linked_field_sets_to_join_query(linked_field_sets: LinkedFieldSetList, data_type_set: Set[str]) -> Optional[Query]:
    if len(linked_field_sets) == 0:
        return None

    # TODO: This blows up combinatorially, oh well.
    pairs = tuple(p for p in itertools.combinations(linked_field_sets[0].items(), 2)
                  if p[0][0] in data_type_set and p[1][0] in data_type_set)

    if len(pairs) == 0:
        return None  # TODO: Somehow tell the user no join was applied or return NO RESULTS if None and 2+ data types?

    if len(linked_field_sets) == 1:
        return _linked_field_set_to_join_query_rec(pairs)

    return ["#and",
            _linked_field_set_to_join_query_rec(pairs),
            _linked_field_sets_to_join_query(linked_field_sets[1:], data_type_set)]


def _get_dataset_linked_field_sets(dataset: dict) -> LinkedFieldSetList:
    return [
        lfs["fields"]
        for lfs in dataset.get("linked_field_sets", [])
        if len(lfs["fields"]) > 1  # Only include useful linked field sets, i.e. 2+ fields
    ]


def _augment_resolves(query: Query, prefix: Tuple[str, ...]) -> Query:
    if not isinstance(query, list) or len(query) == 0 or len(query[0]) == 0 or query[0][0] != "#":
        return query

    if query[0] == "#resolve":
        return ["#resolve", *prefix, *query[1:]]

    return [query[0], *(_augment_resolves(q, prefix) for q in query[1:])]


def _combine_join_and_data_type_queries(join_query: Query, data_type_queries: Dict[str, Query]) -> Query:
    if join_query is None:
        return None

    # Otherwise,  still isn't None...
    # TODO: Pre-filter data_type_results to avoid a billion index combinations - return specific set of combos
    # TODO: Allow passing a non-empty index fixation to search to save time and start somewhere
    # TODO: Or should search filter the data object (including sub-arrays) as it goes, returning it at the end?

    # Combine the join query with data type queries to be able to link across fixed [item]s
    for dt, q in data_type_queries.items():
        join_query = ["#and", _augment_resolves(q, (dt, "[item]")), join_query]

    print(f"[{SERVICE_NAME} {datetime.now()}] [DEBUG] Generated join query: {join_query}", flush=True)

    return join_query


def _get_array_resolve_paths(query: Query) -> List[str]:
    """
    Collect string representations array resolve paths without the trailing [item] resolution from a query. This can
    facilitate determining which index combinations will appear; and can be used as a step in filtering results by
    index combination.
    :param query: Query to collect array resolves from
    :return: List of index combination-compatible array resolve paths.
    """

    if isinstance(query, list) and len(query) > 1:
        r = []

        if query[0] == "#resolve":  # Resolve expression; items make up a resolve path
            r = []
            path = "_root"
            for ri in query[1:]:
                if ri == "[item]":
                    r.append(path)
                path = f"{path}.{ri}"

        else:  # Expression where items are other expressions/literals
            for e in query[1:]:
                r.extend(_get_array_resolve_paths(e))

        return r

    return []


async def _fetch_table_definition_worker(table_queue: Queue, auth_header: Optional[str],
                                         table_ownerships_and_records: List[Tuple[dict, dict]]):
    client = AsyncHTTPClient()

    async for t in table_queue:
        if t is None:
            # Exit signal
            return

        try:
            # TODO: Don't fetch schema except for first time?
            table_ownerships_and_records.append((t, await peer_fetch(
                client,
                CHORD_URL,
                f"api/{t['service_artifact']}/tables/{t['table_id']}",
                method="GET",
                auth_header=auth_header,  # Required, otherwise may hit a 403 error
                extra_headers=DATASET_SEARCH_HEADERS
            )))
            # TODO: Handle HTTP errors

        finally:
            table_queue.task_done()


async def _table_search_worker(
    table_queue: Queue,
    dataset_join_query: Query,
    data_type_queries: Dict[str, Query],
    include_internal_results: bool,
    auth_header: Optional[str],
    dataset_object_schema: dict,
    dataset_results: Dict[str, list],
):
    client = AsyncHTTPClient()

    async for table_pair in table_queue:
        if table_pair is None:
            # Exit signal
            return

        try:
            table_ownership, table_record = table_pair
            table_data_type = table_record["data_type"]
            is_querying_data_type = table_data_type in data_type_queries

            # Don't need to fetch results for joining if the join query is None; just check
            # individual tables (which is much faster) using the public discovery endpoint.
            private = dataset_join_query is not None or include_internal_results

            if dataset_join_query is not None and table_data_type not in dataset_object_schema["properties"]:
                # Since we have a join query, we need to create a superstructure containing
                # different search results and a schema to match.

                # Set schema for data type if needed
                dataset_object_schema["properties"][table_data_type] = {
                    "type": "array",
                    "items": table_record["schema"] if is_querying_data_type else {}
                }

            # If data type is not being queried, its results are irrelevant
            if not is_querying_data_type:
                continue

            r = await peer_fetch(
                client,
                CHORD_URL,
                path_fragment=(
                    f"api/{table_ownership['service_artifact']}{'/private' if private else ''}/tables"
                    f"/{table_record['id']}/search"
                ),
                request_body=json.dumps({"query": data_type_queries[table_data_type]}),
                method="POST",
                auth_header=auth_header,  # Required in some cases to not get a 403
                extra_headers=DATASET_SEARCH_HEADERS,
            )

            if private:
                # We have a results array to account for
                results = r["results"]
            else:
                # Here, the array of 1 True is a dummy value to give a positive result
                results = [r] if r else []

            if table_data_type not in dataset_results:
                dataset_results[table_data_type] = results
            else:
                dataset_results[table_data_type].extend(results)

        finally:
            table_queue.task_done()


async def run_search_on_dataset(
    dataset_object_schema: dict,
    dataset: dict,
    join_query: Query,
    data_type_queries: Dict[str, Query],
    include_internal_results: bool,
    auth_header: Optional[str] = None,
) -> Tuple[Dict[str, list], Query, List[str]]:
    linked_field_sets: LinkedFieldSetList = _get_dataset_linked_field_sets(dataset)

    # Pairs of table ownership records, from the metadata service, and table records,
    # from each data service to which the table belongs)
    table_ownerships_and_records: List[Tuple[Dict, Dict]] = []

    table_ownership_queue = iterable_to_queue(dataset["table_ownership"])

    table_definition_workers = tornado.gen.multi([
        _fetch_table_definition_worker(table_ownership_queue, auth_header, table_ownerships_and_records)
        for _ in range(WORKERS)
    ])
    await table_ownership_queue.join()

    try:
        table_data_types: Set[str] = {t[1]["data_type"] for t in table_ownerships_and_records}
        excluded_data_types: Set[str] = set()

        for dt, dt_q in filter(lambda dt2: dt2[0] not in table_data_types, data_type_queries.items()):
            # If there are no tables of a particular data type, we don't get the schema. If this
            # happens, return no results unless the query is hard-coded to be True, in which
            # case put in a fake schema.
            # TODO: Come up with something more elegant/intuitive here - a way to resolve data types?
            # TODO: This may sometimes return the wrong result - should check for resolves instead

            # This CANNOT be simplified to "if not dt_q:"; other truth-y values don't have the
            # same meaning (sorry Guido).
            if dt_q is not True:
                return {dt2: [] for dt2 in data_type_queries}, None, []

            # Give it a boilerplate array schema and result set; there won't be anything there anyway
            dataset_object_schema["properties"][dt] = {"type": "array"}
            excluded_data_types.add(dt)

            print(f"[{SERVICE_NAME} {datetime.now()}] [DEBUG] Excluding data type: {dt}", flush=True)

        if join_query is None:
            # Could re-return None; pass set of all data types (keys of the data type queries)
            # to filter out combinations
            join_query = _linked_field_sets_to_join_query(
                linked_field_sets, set(data_type_queries) - excluded_data_types)

        # Figure out what index combinations we'll need to filter along from the join query,
        # BEFORE we combine the join query with the data type queries to create the compound
        # query used to find the actual results.
        ic_paths_to_filter = _get_array_resolve_paths(join_query) if include_internal_results else []

        # Combine the join query with the data type queries, fixing resolves to be consistent
        join_query = _combine_join_and_data_type_queries(join_query, data_type_queries)

    finally:
        # Trigger exit for all table definition workers
        for _ in range(WORKERS):
            table_ownership_queue.put_nowait(None)

        # Wait for table definition workers to exit
        await table_definition_workers

    # ------------------------- Start running search on tables -------------------------

    dataset_results: Dict[str, list] = {}

    table_pairs_queue = iterable_to_queue(table_ownerships_and_records)

    table_search_workers = tornado.gen.multi([
        _table_search_worker(
            table_pairs_queue,
            join_query,
            data_type_queries,
            include_internal_results,
            auth_header,
            dataset_object_schema,
            dataset_results,
        )
        for _ in range(WORKERS)
    ])

    await table_pairs_queue.join()

    # Trigger exit for all table search workers
    for _ in range(WORKERS):
        table_pairs_queue.put_nowait(None)

    # Wait for table search workers to exit
    await table_search_workers

    # Return dataset-level results to calculate final result from
    # Return dataset join query for later use (when generating results)
    # Return index combination paths to filter by (for returning a proper result-set)
    return dataset_results, join_query, ic_paths_to_filter
