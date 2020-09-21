"""
This module contains the design pattern "COMMANDS" to execute VQL query . 

Each VQL statement corresponds to a <name>_cmd() fonction. 
You can use `execute(conn, vql)` or `execute_many(conn,vql)` to run a specific VQL query.
Command module are usefull for CLI and for running VQL scripts .

Examples:

    conn = sqlite.Connection("project.db") 
    for variant in execute(conn, "SELECT chr, pos FROM variants"):
        print(variant)

    # How many variant 
    print(execute(conn, "COUNT FROM variants"))


"""
from cutevariant.core.querybuilder import *
from cutevariant.core import sql, vql
import sqlite3
import networkx as nx
import os
import functools
import csv

from memoization import (
    cached,
)  # Pip install ( because functools doesnt work with unhachable)

from cutevariant.commons import logger


LOGGER = logger()


def select_cmd(
    conn: sqlite3.Connection,
    fields=["chr", "pos", "ref", "alt"],
    source="variants",
    filters=dict(),
    order_by=None,
    order_desc=True,
    group_by=[],
    having={},  # {"op":">", "value": 3  }
    limit=50,
    offset=0,
    **kwargs,
):
    """Select query Command 

    This following VQL command:
        SELECT chr,pos FROM variants WHERE pos > 3 
    will execute : 
        select_cmd(conn, ["chr", "pos"], variants", {"AND": [{"pos","=",3}]}))

        
        Args:
            conn (sqlite3.Connection): sqlite3 connection 
            fields (list, optional): list of fields 
            filters (dict, optional): nested tree of condition 
            source (str, optional): virtual table source
            order_by (list, optional): order by field name 
            order_desc (bool, optional): Descending or Ascending Order  
            limit (int, optional): record count 
            offset (int, optional): record count per page  
        
        Yields:
            list of variants   
        """
    query = build_complete_query(
        conn,
        fields,
        source,
        filters,
        order_by,
        order_desc,
        group_by,
        having,
        limit,
        offset,
        **kwargs,
    )

    LOGGER.debug(query)
    for i in conn.execute(query):
        yield dict(i)


@cached(max_size=128)
def count_cmd(
    conn: sqlite3.Connection,
    fields=["chr", "pos", "ref", "alt"],
    source="variants",
    filters={},
    group_by=[],
    having={},
    **kwargs,
):
    """Count command 

    This following VQL command:
        COUNT FROM variants WHERE pos > 3 
   will execute : 
        count_cmd(conn, "variants", {"AND": [{"pos","=",3}]}))

    
    Args:
        conn (sqlite3.Connection): sqlite3 connection
        source (str, optional): virtual source table  
        filters (dict, optional): nested tree of condition  
    
    Returns:
        dict : return count of variant with "count" as a key 
    """

    if not filters:
        # Returned stored cache variant
        return {
            "count": conn.execute(
                f"SELECT count FROM selections WHERE name = '{source}'"
            ).fetchone()[0]
        }

    default_tables = dict([(i["name"], i["category"]) for i in sql.get_fields(conn)])
    samples_ids = dict([(i["name"], i["id"]) for i in sql.get_samples(conn)])
    query = build_query(
        fields=fields,
        source=source,
        filters=filters,
        limit=None,
        offset=None,
        order_desc=None,
        order_by=None,
        group_by=group_by,
        having=having,
        default_tables=default_tables,
        samples_ids=samples_ids,
    )
    # #    print("ICI", query, query[from_pos:])

    # print("ICI", filters)
    # if distinct:
    #     query = (
    #         "SELECT COUNT (*) FROM (SELECT DISTINCT variants.id "
    #         + query[from_pos:]
    #         + ")"
    #     )
    # else:

    query = "SELECT COUNT (*) FROM (  " + query + ")"
    return {"count": conn.execute(query).fetchone()[0]}


def drop_cmd(conn: sqlite3.Connection, feature: str, name: str, **kwargs):
    """Drop selection or set from database 
    
    This following VQL command:
        DROP selection boby 
   will execute : 
        drop_cmd(conn, "selections", "boby")


    Args:
        conn (sqlite3.Connection): sqlite.Connection
        feature (str): selection or set  
        name (str): name of the selection or the set 
    
    Returns:
        dict: return {success: True}
    
    Raises:
        vql.VQLSyntaxError: Description
    
    """
    accept_features = ["selections", "sets"]

    if feature not in accept_features:
        raise vql.VQLSyntaxError(f"{feature} doesn't exists")

    if feature == "selections":
        conn.execute(f"DELETE FROM selections WHERE name = '{name}'")
        conn.commit()
        return {"success": True}

    if feature == "sets":
        res = conn.execute(f"DELETE FROM sets WHERE name = '{name}'")
        conn.commit()
        return {"success": True}


def create_cmd(
    conn: sqlite3.Connection,
    target: str,
    source="variants",
    filters=dict(),
    count=0,
    **kwargs,
):
    """Create command 

    This following VQL command:
        CREATE boby FROM variants WHERE pos > 3  
   will execute : 
        create_cmd(conn, "boby", "variants", {"AND":[{"pos",">",3}]})
    
    Args:
        conn (sqlite3.Connection): sqlite3 Connection
        target (str): target selection table
        source (str): source selection table
        filters (TYPE): filters query 
        count (int): precomputed variant count 
    
    Returns:
        dict: {success: True} 
    """
    default_tables = dict([(i["name"], i["category"]) for i in sql.get_fields(conn)])
    samples_ids = dict([(i["name"], i["id"]) for i in sql.get_samples(conn)])

    if target is None:
        return {}

    cursor = conn.cursor()

    sql_query = build_query(
        ["id"],
        source,
        filters,
        default_tables=default_tables,
        samples_ids=samples_ids,
        limit=None,
    )

    count = sql.count_query(conn, sql_query)

    selection_id = sql.insert_selection(cursor, sql_query, name=target, count=count)

    q = f"""
    INSERT INTO selection_has_variant
    SELECT DISTINCT id, {selection_id} FROM ({sql_query})
    """

    # DROP indexes
    # For joints between selections and variants tables
    try:
        cursor.execute("""DROP INDEX idx_selection_has_variant""")
    except sqlite3.OperationalError:
        pass

    cursor.execute(q)

    LOGGER.debug(q)

    # # REBUILD INDEXES
    # # For joints between selections and variants tables
    sql.create_selection_has_variant_indexes(cursor)

    conn.commit()

    if cursor.rowcount:
        return {"id": cursor.lastrowid}
    return {}


def set_cmd(
    conn: sqlite3.Connection, target: str, first: str, second: str, operator, **kwargs
):
    """Perform set operation like intersection, union and difference between two table selection

    This following VQL command:
        CREATE boby = raymond & charles
   will execute : 
        set_cmd(conn, "boby", "raymond", "charles", "&")


    Args:
        conn (sqlite3.Connection): sqlite3.Connection
        target (str): table selection target
        first (str): first selection in operation
        second (str): second selection in operation
        operator (str): + (union), - (difference), & (intersection)
    
    Returns:
        dict: {success: True}
    """
    if target is None or first is None or second is None or operator is None:
        return {}

    cursor = conn.cursor()

    query_first = build_query(["id"], first, limit=None)
    query_second = build_query(["id"], second, limit=None)

    if operator == "+":
        sql_query = sql.union_variants(query_first, query_second)

    if operator == "-":
        sql_query = sql.subtract_variants(query_first, query_second)

    if operator == "&":
        sql_query = sql.intersect_variants(query_first, query_second)

    count = sql.count_query(conn, sql_query)
    selection_id = sql.insert_selection(cursor, sql_query, name=target, count=count)

    q = f"""
    INSERT INTO selection_has_variant
    SELECT DISTINCT id, {selection_id} FROM ({sql_query})
    """

    # DROP indexes
    # For joints between selections and variants tables
    try:
        cursor.execute("""DROP INDEX idx_selection_has_variant""")
    except sqlite3.OperationalError:
        pass

    cursor.execute(q)

    # # REBUILD INDEXES
    # # For joints between selections and variants tables
    sql.create_selection_has_variant_indexes(cursor)

    conn.commit()
    if cursor.rowcount:
        return {"id": cursor.lastrowid}
    return {}


def bed_cmd(conn: sqlite3.Connection, path: str, target: str, source: str, **kwargs):
    """Create a new selection from a bed file 
    
    This following VQL command:
        CREATE boby FROM variant INTERSECT "path/to/file.bed"
   will execute : 
        bed_cmd(conn, "path/to/file.bed", "boby", "source")


    Args:
        conn (sqlite3.Connection): sqlite3.Connection
        path (str): path to bedfile ( a 3 columns files with chr, start, end )
        target (str): target selection table
        source (str): source selection table 
        **kwargs: Description
    
    Returns:
        TYPE: Description
    
    Raises:
        vql.VQLSyntaxError: Description
    """
    if not os.path.exists(path):
        raise vql.VQLSyntaxError(f"{path} doesn't exists")

    def read_bed():
        with open(path) as file:
            reader = csv.reader(file, delimiter="\t")
            for line in reader:
                if len(line) >= 3:
                    yield {
                        "chr": line[0],
                        "start": int(line[1]),
                        "end": int(line[2]),
                        "name": "",
                    }

    selection_id = sql.create_selection_from_bed(conn, source, target, read_bed())
    return {"id": selection_id}


def show_cmd(conn: sqlite3.Connection, feature: str, **kwargs):
    """Show command display information from a SHOW query 

    This following VQL command:
        SHOW variants
   will execute : 
        show_cmd(conn, "variants")

    Args:
        conn (sqlite3.Connection): sqlite3.Connection
        feature (str): ["selections", "fields", "samples", "sets"]
    
    Yields:
        dict: record items
    
    Raises:
        vql.VQLSyntaxError: Description
    """
    accept_features = ["selections", "fields", "samples", "sets"]

    if feature not in accept_features:
        raise vql.VQLSyntaxError(f"option {feature} doesn't exists")

    if feature == "fields":
        for field in sql.get_fields(conn):
            yield field

    if feature == "samples":
        for sample in sql.get_samples(conn):
            yield sample

    if feature == "selections":
        for selection in sql.get_selections(conn):
            yield selection

    if feature == "sets":
        for item in sql.get_sets(conn):
            yield item


def import_cmd(conn: sqlite3.Connection, feature=str, name=str, path=str, **kwargs):
    """Import command 

    This following VQL command:
        IMPORT sets "gene.txt" AS boby
   will execute : 
        import_cmd(conn, "sets", "gene.txt")
    
    Args:
        conn (sqlite3.Connection): sqlite3.Connection
        feature (TYPE): "set"  
        name (TYPE): name of the set
        path (TYPE): a filepath 
        **kwargs: Description
    
    Returns:
        dict: {success: True}
    
    Raises:
        vql.VQLSyntaxError: Description
    """
    accept_features = ["sets"]
    if feature not in accept_features:
        raise vql.VQLSyntaxError(f"option {feature} doesn't exists")

    if os.path.exists(path):
        sql.insert_set_from_file(conn, name, path)
    else:
        raise vql.VQLSyntaxError(f"{path} doesn't exists")

    return {"success": True}


def create_command_from_obj(conn, vql_obj: dict):
    """Create command function from vql object.

    vql object are dictionnary returns by vql.parse

    Use command.execute instead 
    
    Args:
        conn (sqlite3.Connection): sqlite3.connection
        vql_obj (dict): a vql object 
    
    Returns:
        Function: Command function
    """
    if vql_obj["cmd"] == "select_cmd":
        return functools.partial(select_cmd, conn, **vql_obj)

    if vql_obj["cmd"] == "create_cmd":
        return functools.partial(create_cmd, conn, **vql_obj)

    if vql_obj["cmd"] == "set_cmd":
        return functools.partial(set_cmd, conn, **vql_obj)

    if vql_obj["cmd"] == "bed_cmd":
        return functools.partial(bed_cmd, conn, **vql_obj)

    if vql_obj["cmd"] == "show_cmd":
        return functools.partial(show_cmd, conn, **vql_obj)

    if vql_obj["cmd"] == "import_cmd":
        return functools.partial(import_cmd, conn, **vql_obj)

    if vql_obj["cmd"] == "drop_cmd":
        return functools.partial(drop_cmd, conn, **vql_obj)

    if vql_obj["cmd"] == "count_cmd":
        return functools.partial(count_cmd, conn, **vql_obj)

    return None


def execute(conn: sqlite3.Connection, vql_source: str):
    """Execute a vql query

    for variant in execute(conn,"SELECT chr from variants"):
        print(variant)
    
    Args:
        conn (sqlite3.Connection): sqlite3 Connection
        vql_source (str): a VQL query
    
    Returns:
        dict: Return command output as a dict 
    """
    vql_obj = vql.parse_one_vql(vql_source)
    cmd = create_command_from_obj(conn, vql_obj)
    return cmd()


def execute_all(conn: sqlite3.Connection, vql_source: str):
    """Execute a vql script 
    
    execute_all("CREATE boby FROM variants; CREATE raymon FROM variants; CREATE charles = boby - variants; COUNT(charles)")
    
    Args:
        conn (sqlite3.Connection): Description
        vql_source (str): Description
    
    Yields:
        dict: Yield command output as a dict
    """
    for vql in vql.parse_vql(vql_source):
        cmd = create_command_from_obj(conn, vql)
        yield cmd()


# class CommandGraph(object):
#     def __init__(self, conn):
#         super().__init__()
#         self.conn = conn
#         self.graph = nx.DiGraph()
#         self.graph.add_node("variants")

#     def add_command(self, command: Command):

#         if type(command) == CreateCommand:
#             self.graph.add_node(command.target)
#             self.graph.add_edge(command.source, command.target)

#         if type(command) == SelectCommand:
#             self.graph.add_node("Select")
#             self.graph.add_edge(command.source, "Select")

#         if type(command) == SetCommand:
#             self.graph.add_node(command.target)
#             self.graph.add_edge(command.first, command.target)
#             self.graph.add_edge(command.second, command.target)

#     def set_source(self, source):
#         self.graph.clear()
#         for vql_obj in vql.execute_vql(source):
#             cmd = create_command_from_vql_objet(self.conn, vql_obj)
#             self.add_command(cmd)
