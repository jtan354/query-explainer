import sqlparse
from igraph import Graph
import json_diff
import re
import math
from datetime import date
from datetime import timedelta

def getConstant(cursor, relation, attribute, selectivity, isDate):
    stmt = "SELECT reltuples FROM pg_class WHERE relname = '{}'".format(relation)
    cursor.execute(stmt)
    noOfTuples, = cursor.fetchall()[0]

    stmt2 = "SELECT histogram_bounds FROM pg_stats WHERE tablename = '{}' AND attname = '{}'".format(relation,
                                                                                                     attribute)
    cursor.execute(stmt2)
    results = cursor.fetchall()
    results2, = results[0]
    bounds = results2[1:-1].split(",")
    selPerBucket = 1 / (len(bounds) - 1)
    # print("sel per bucket: ",selPerBucket)
    # print("selectivity: ",selectivity)
    left = math.floor(selectivity / selPerBucket)
    remainder = selectivity - (left * selPerBucket)
    scale = remainder / selPerBucket
    if isDate:
        # print("left: ",left)
        # print("len of bounds: ",len(bounds))
        lowerBound = bounds[left]
        upperBound = bounds[left + 1]
        s = lowerBound.split('-')
        s2 = upperBound.split('-')
        s_date = date(int(s[0]), int(s[1]), int(s[2]))
        s2_date = date(int(s2[0]), int(s2[1]), int(s2[2]))
        delta = ((s2_date - s_date).days) * scale
        k = (s_date + timedelta(days=delta)).strftime("%Y-%m-%d")
    else:
        lowerBound = float(bounds[left])
        upperBound = float(bounds[left + 1])
        k = lowerBound + (upperBound - lowerBound) * scale

    return k

def planIsSame(jsonFile1, jsonFile2):
    # tree.json is the json file output from the EXPLAIN query
    # jsonFile1 = open("tree1.json")
    # jsonFile2 = open("tree2.json")
    # you need to pip install json_diff
    c = json_diff.Comparator(jsonFile1, jsonFile2)
    diff = c.compare_dicts()
    return (diff == {})


def explainPositionChange(chosen, alt, alias, isVarying, isHigherSel):
    for element in chosen:
        if element[0] == alias:
            chosen = element
    for element in alt:
        if element[0] == alias:
            alt = element

    if isVarying and isHigherSel:
        if chosen[2] > alt[2]:
            return ("If its sister relation is different, it is because another relation with a lower selectivity which can output a smaller intermediate join result than " + alias + ". So better to join " + alias + "in later Joins. If its sister relation is the same in both plans, it means that joining with this sister relation produces the smallest intermediate output, among all other available relations. The alternate plan would be better if " + alias + " had a lower selectivity. ")
        elif chosen[2] < alt[2]:
            return ("The chosen plan is more suitable than alternate plan because " + alias + " has a higher selectivity. " + alias + " is joined earlier despite having a higher selectivity because the query optimiser thinks that joining this earlier, instead of with other relations, can produce the smallest intermediate relation using the formula T(Outer Relation)*T(Inner Relation) / max ( V (Outer Relation , Join Attribute) , V (Inner Relation, Join Attribute) ) i.e. " + alias + " may have a much higher V (Relation, Join Attribute) than other relations. ")
    elif isVarying and not isHigherSel:
        if chosen[2] < alt[2]:
            return (alias + " is joined earlier in the chosen plan because it will output a smaller intermediate result than if other relations were used for joining. The alternate plan would be better if " + alias + " had a higher selectivity. ")
        elif chosen[2] > alt[2]:
            return (alias + " is joined later (compared to in the alternate plan) despite having a lower selectivity because the query optimiser thinks that there is another relation can produce the smallest intermediate relation using the formula T(Outer Relation)*T(Inner Relation) / max ( V (Outer Relation , Join Attribute) , V (Inner Relation, Join Attribute) ) i.e. " + alias + " may have a much higher T (Relation) / V (Relation, Join Attribute) ratio compared to other relations suitable for the join operation. ")
    elif not isVarying:
        if chosen[2] < alt[2]: # if relation is joined earlier in chosen than in alt
            return(alias + " is joined earlier because it can output the smallest intermediate relation, compared to other relations available for joining. This allows future joins later in the plan to be cheaper. ")
        elif chosen[2] > alt[2]:
            return("There is another relation that can produce a smaller intermediate relation than " + alias + ". Joining " + alias + " later as compared to in the alternate plan would allow smaller intermediate relations to be generated by earlier join operations involing other relations. ")

def explainJoinChange(chosen, alt, alias):
    for element in chosen:
        if element[0] == alias:
            chosen = element
    for element in alt:
        if element[0] == alias:
            alt = element

    if alt[3] == "Hash Join" and chosen[3] == "Nested Loop" and chosen[4] == "Inner" and chosen[
        5] == "Index Scan":  # if Hash Join >>> Nested Loop Inner
        return (
            "Better to use Nested Loop than Hash Join because Hash Join requires scanning in the entire child relations, while being inner relation of Nested Loop Join just requires Index Scan over relatively few tuples, since the outer relation in the join is expected to be relatively small.")

    elif alt[3] == "Nested Loop" and alt[4] == "Inner" and alt[5] == "Index Scan" and chosen[
        3] == "Hash Join":  # if Nested Loop Inner >>> Hash Join
        return (
                    "Better to use Hash Join than Nested Loop because the outer child relation in Nested Loop Join is too big and would result in too many Index Scan probes. It is cheaper to simply Seq Scan the entire relation of " + alias + ". ")

    elif alt[3] == "Hash Join" and chosen[3] == "Nested Loop" and alt[4] == chosen[4] == "Outer":
        return (
                    "Nested Loop is better than Hash Join because " + alias + " is expcted to be small, so using a Nested Loop join would result in relatively few Index Scan probes into the inner relation of the join operation. Index Scans are more expensive than Seq Scan per tuple retrieved, but it is cheaper to use Index Scan on a few tuples than to do Seq Scan on an entire relation. ")

    elif alt[3] == "Nested Loop" and chosen[3] == "Hash Join" and alt[4] == chosen[4] == "Outer":
        return (
                    "Hash Join is better than Nested Loop Join because " + alias + " is expected to have many tuples, so using Nested Join would result in several Index Scan probes into the inner relation. It is cheaper to use Seq Scan on the entire inner relation, as it is done in Hash Join in the chosen plan. ")

    elif alt[3] == "Hash Join" and chosen[3] == "Hash Join":  # if Hash Join >>> Hash Join
        if alt[4] == "Outer" and chosen[4] == "Inner":  # if HJ Outer >>> HJ Inner
            return (
                " is the inner relation because it is expected to be bigger than the outer relation of the Hash Join. The smaller relation is hashed first, to allow the possibility of Hybird Hash Join. ")
        elif alt[4] == "Inner" and chosen[4] == "Outer":  # if HJ Inner >>> HJ Outer
            return (
                        alias + " is the outer relation because it is expected to be smaller than the inner relation of the Hash Join. The smaller relation is hashed first, to allow the possibility of Hybird Hash Join. ")

    elif alt[3] == chosen[3] == "Nested Loop":  # if Nested Loop >>> Nested Loop
        if alt[4] == "Inner" and chosen[4] == "Outer":  # if Nested Loop Inner >>> Nested Loop Outer
            return (
                        alias + " is better suited to be the outer relation (instead of inner relation as in the alternate plan) because the relation is expected to be smaller than its sister relation. The smaller relation is better suited to be the otuer relation so that there will be fwer probes done onto the inner relation, as compared to if it were the other way around. ")
        elif alt[4] == "Outer" and chosen[4] == "Inner":  # if Nested Loop Outer >>> Nested Loop Inner
            return (
                        alias + " is better suited to be the inner relation (instead of outer relation as in the alternate plan) because the relation is expected to be bigger than its sister relation. The smaller relation is better suited to be the otuer relation so that there will be fwer probes done onto the inner relation, as compared to if it were the other way around. ")

    elif "Merge" in chosen[3]:  # Nested Loop >>> Merge
        if alt[3] == "Nested Loop":
            return (
                "Both relations in the join are expected to have many rows, so using either relation as the outer relation for Nested Loop Join like in the alternate plan would require many probes into the inner relation of the join. Since the final results needs to be eventually sorted, Merge Join may require Index Scans on both relations, but the cost savings from Merge Join's sorting will allow overall I/O cost savings. ")
        else:
            return (
                "Both relations in the join are expected to have many rows. Since the final results needs to be eventually sorted, Merge Join may require Index Scans on both relations, but the cost savings from Merge Join's sorting will allow overall I/O cost savings. ")

    elif "Merge" in alt[3]:  # Merge >>> Nested Loop
        if chosen[3] == "Nested Loop":
            return (
                "One of the relations is expected to have relatively fewer rows, so it is cheaper to do a Seq Scan on the outer relation, followed by Index Scan probes onto the inner relation, rather than doing Index Scans on both relations. ")
        elif chosen[3] == "Hash Join":
            return (
                "One of the relations is expected to have relatively fewer rows, so there is the possibility of using Hybrid Hash Join that is cheaper to execute than Merge Sort, even if Hash Join does not carry out any intermediate sorting for the final result output. ")

def generateIGraph(operationsArray):
    g = Graph()
    nodes = []
    # node = [Node Type, Join Order, Index of Parent]
    for index, node in enumerate(operationsArray):
        g.add_vertex()
        g.vs[index]["Node Type"] = node[0]
        nodes.append(node[0])
        g.vs[index]["Join Order"] = node[1]
        g.vs[index]["Parent"] = node[2]
        if (node[2] != -1):  # if node is not the root
            g.add_edge( g.vs[index]["Parent"], index)

    return g,nodes


def getModifiedQuery(column, value, originalSqlText, isDate):
    # column = name of the table column
    # value = value of the column that you want to set
    # originalSqlText = open("./query1.sql", "r").read()
    newText = sqlparse.format(originalSqlText, keyword_case="lower", identifier_case="lower", reindent=True,
                              reindent_aligned=True, use_space_around_operators=True)
    textArray = newText.split("\n")

    for index, line in enumerate(textArray):  # for each line in the input query
        if column in line:  # if the column is mentioned in the line
            if (line[-1] == "=" or line[-1] == "<" or line[-1] == ">"):
                continue
            if (">" in line or "between" in line):
                textArray.remove(line)
                continue
            if "<" in line or "<=" in line:
                if isDate:
                    textArray[index] = "and {} <= date '{}'".format(column, value)
                else:
                    textArray[index] = "and {} <= {}".format(column, value)

    revisedQuery = " ".join(textArray)
    revisedQuery = sqlparse.format(revisedQuery, keyword_case="lower", identifier_case="lower", reindent=True,
                                   reindent_aligned=True, use_space_around_operators=True)

    # use the cursor to execute this new query. Need to put an EXPLAIN in front of this query
    return revisedQuery


def getSelectivityArray(textArray,cursor):
    # cursorResults = cursor.fetchall()
    # this function returns an array. Each array element is an array that looks like
    # [relationName, columnName, selectivity]

    lineitemVars = ["lineitem", "l_orderkey", "l_partkey", "l_suppkey", "l_linenumber", "l_quantity", "l_extendedprice",
                    "l_discount", "l_tax", "l_shipdate", "l_receipt"]
    customerVars = ["customer", "c_custkey", "c_nationkey", "c_acctbal"]
    nationVars = ["nation", "n_nationkey", "n_regionkey"]
    ordersVars = ["orders", "o_orderkey", "o_custkey", "o_totalprice", "o_orderdate", "o_shippingpriority"]
    partVars = ["part", "p_partkey", "p_size", "p_retailprice"]
    partsuppVars = ["partsupp", "ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost"]
    regionVars = ["region", "r_regionkey"]
    supplierVars = ["supplier", "s_suppkey", "s_nationkey", "s_acctbal"]
    allRelations = [lineitemVars, customerVars, nationVars, ordersVars, partVars, partsuppVars, regionVars,
                    supplierVars]

    variablesFound = list()
    relationSelectivity = list()  # each element of this array is a relation, selectivity ratio pair

    for subText, in textArray:
        if (
                "Filter" in subText and "::" in subText):  # find filters that correspond to numeric/string values, not subplans
            if ("::numeric" in subText or "::integer" in subText or "::date" in subText):
                # print(subText)
                if ("<" in subText or ">" in subText or "<=" in subText or ">=" in subText):
                    pattern = "::(.*?)\)"
                    annotations = re.findall(pattern, subText)
                    condition = (re.search("\((.*)\)", subText)).group(1)
                    dataType = ""
                    # print("pre condition " + condition)
                    if "date" not in subText:
                        condition = condition.replace("'", "")
                    for varType in annotations:
                        dataType = varType
                        toRemove = "::" + varType
                        condition = condition.replace(toRemove, "")
                        # if dataType == "date":
                        #     pattern = "'(.*?)'"
                        #     dateValue = (re.search(pattern, condition)).group(1)
                        #     toRemove = "'" + dateValue + "'"
                        #     condition = condition.replace(toRemove, ("date " + toRemove))
                    if ("<" in condition or ">" in condition or "<=" in condition or ">=" in condition):
                        for relation in allRelations:
                            if relation[0] in condition:
                                continue
                            for column in relation:
                                if column in condition:
                                    variablesFound.append([relation[0], column, condition, dataType])

    for element in variablesFound:
        relation = element[0]
        column = element[1]
        condition = element[2]
        dataType = element[3]

        stmt = "SELECT COUNT(*) FROM {} WHERE {}".format(relation, condition)

        cursor.execute(stmt)
        results, = cursor.fetchall()[0]

        stmt = "SELECT COUNT(*) FROM {}".format(relation)
        cursor.execute(stmt)
        total, = cursor.fetchall()[0]

        if dataType == "date":
            relationSelectivity.append([relation, column, results / total, True])
        else:
            relationSelectivity.append([relation, column, results / total, False])

    return (variablesFound, relationSelectivity)

