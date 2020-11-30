import dash
import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objs as go
import psycopg2
import ast
from igraph import Graph, EdgeSeq
import json
import base64
from dash.dependencies import Input, Output, State
from util_functions import *
from dash.exceptions import PreventUpdate


counter = 1
operations = list()
joinOnlyCounter = 1
signatures_global = []
joins_global = []


def generateOpSeq(subRoot, parentIndex):  # note the use of global variables, sorry about that
    global joinOnlyCounter
    global operations

    operations.append([subRoot["Node Type"], "None", parentIndex])
    indexOfThis = len(operations) - 1

    for child in subRoot["Plans"]:
        if "Alias" not in child and len(child["Plans"]) > 1:  # if this child is a leaf node, skip this first
            # if len(child["Plans"]) > 1: # explore the child that has more than one child first
            generateOpSeq(child, indexOfThis)
    for child in subRoot["Plans"]:
        if "Alias" not in child and len(child["Plans"]) == 1:
            # if len(child["Plans"]) == 1: # if this node has only one NON-LEAF child
            generateOpSeq(child, indexOfThis)
    for child in subRoot["Plans"]:
        if "Alias" not in child and len(child["Plans"]) == 1 and "Alias" in child["Plans"][0]:
            generateOpSeq(child, indexOfThis)
    for child in subRoot["Plans"]:
        if "Alias" in child and "Plans" in child:
            generateOpSeq(child, indexOfThis)
    for child in subRoot["Plans"]:
        if "Alias" in child and "Plans" not in child:
            operations.append([child["Relation Name"] + " (" + child["Node Type"] + ")", joinOnlyCounter, indexOfThis])
    # once it is here, this node has transversed all its children
    if ("Join" in subRoot["Node Type"] or "Nested" in subRoot["Node Type"]):
        joinOnlyCounter += 1

    return


def exploreChildren(subRoot):
    global counter
    global signatures_global
    global joins_global

    if ("Join" in subRoot["Node Type"] or "Nested" in subRoot["Node Type"]):
        joins_global.append(subRoot["Node Type"])

    for child in subRoot["Plans"]:
        if "Alias" not in child and len(child["Plans"]) > 1:  # if this child is a leaf node, skip this first
            # if len(child["Plans"]) > 1: # explore the child that has more than one child first
            print("adding " + child["Node Type"])
            exploreChildren(child)
    for child in subRoot["Plans"]:
        if "Alias" not in child and len(child["Plans"]) == 1 and "Alias" not in child["Plans"][0]:
            print("adding " + child["Node Type"])
            exploreChildren(child)
    for child in subRoot["Plans"]:
        if "Alias" not in child and len(child["Plans"]) == 1 and "Alias" in child["Plans"][0]:
            print("adding " + child["Node Type"])
            exploreChildren(child)
    for child in subRoot["Plans"]:
        if "Alias" in child and "Plans" in child:
            print("adding " + child["Node Type"])
            exploreChildren(child)
    for child in subRoot["Plans"]:
        if "Alias" in child and "Plans" not in child:
            print("adding " + child["Node Type"])
            if (len(joins_global) >= 1):
                signatures_global.append(
                    [child["Alias"], child["Relation Name"], counter, joins_global[-1], child["Parent Relationship"],
                     child["Node Type"]])
            else:
                signatures_global.append([child["Alias"], child["Relation Name"], counter, None, child["Parent Relationship"],
                                   child["Node Type"]])

    # once it is here, this node has transversed all its children
    if ("Join" in subRoot["Node Type"] or "Nested" in subRoot["Node Type"]):
        counter += 1
        joins_global.pop()
    # print(signatures)
    return


external_stylesheets = ['https://codepen.io/chriddyp/pen/bWLwgP.css']
app = dash.Dash(__name__, external_stylesheets=external_stylesheets)
available_variables = []

app.layout = html.Div([
    html.Div([

        html.Div([
            html.H1('Query Explainer',style={'color': 'white', 'fontFamily': 'Lucida Sans Unicode','fontSize': 50}),
            dcc.Upload(id = "sql-file",children = html.Button('Upload SQL File',style={'color': 'white','border-color':'white'}),accept ='.sql')
        ],
        style={'width': '49%', 'display': 'inline-block'}),

        html.Div([
            html.H2('Explanation of Query Plan',style={'color': 'white', 'fontFamily': 'Lucida Sans Unicode','fontSize': 30}),
            html.Blockquote(id = "explanation")
        ], style={'width': '49%', 'float': 'right', 'display': 'inline-block'})

    ], style={
        'borderBottom': 'thin lightgrey solid',
        'backgroundColor': 'rgb(110, 152, 135)',
        'padding': '10px 5px'
    }),
    html.Div([html.Button("Increase Selectivity",id = "increase-sel",n_clicks = 0),
              html.Button("Decrease Selectivity",id = "decrease-sel",n_clicks = 0)
             ],
             style= {'width':'49%'},
             ),
    html.Div([html.H1(id = "selectivity-value-chosen")],
    style= {'width':'49%'},
             ),
    html.Div(id = 'upload-data',
             style={'vertical-align':'top','display':'inline-block','width': '49%', 'padding': '0 20'}),
    html.Div([html.H1(id = "selectivity-value-alt")],
        style= {'width':'49%'},
                 ),
    html.Div(id='alt-plan', style={'vertical-align':'top','display': 'inline-block'}),
    html.Div(dcc.Dropdown(
                id='available-variables',
                options=[{'label': i, 'value': i} for i in available_variables],
                placeholder="Choose a variable...",
            ),
            style={'width': '49%', 'padding': '0 20'}
    ),
    html.Div(id = "variable-vertices", style = {'width': '49%'}),
    html.Div(id='intermediate-value', style={'display': 'none'}),
    html.Div(id='chosen-relation', style={'display': 'none'}),
    html.Div(id='node-names', style={'display': 'none'}),
    html.Div(id='chosen-date', style={'display': 'none'}),
    html.Div(id='initial-query-plan', style={'display': 'none'}),
    html.Div(id='alt-plan-signatures', style={'display': 'none'}),
    html.Div(id='initial-plan-signatures', style={'display': 'none'}),
    html.Div(id='isHigherSel', style={'display': 'none'}),
    html.Div(id='isVarying', style= {'display': 'none'}),
    html.Div(id='alias-relation', style= {'display': 'none'})


])

@app.callback(
                Output("explanation","children"),
                Input('all-vertice-variables', 'value'),
                State('initial-plan-signatures','children'),
                State('alt-plan-signatures', 'children'),
                State("isHigherSel","children"),
                State('available-variables','value'),
                State('isVarying','children'),
                State("alias-relation",'children')
              )

def explanation_output(chosen_relation,init_plan,alt_plan,isHigherSel,chosen_var,isVarying,alias_relation):

    if not chosen_relation and not init_plan and not alt_plan and not isHigherSel:
        raise PreventUpdate

    else:
        alias_relation_dict = json.loads(alias_relation)
        isVaryDict = json.loads(isVarying)

        print("init : ",init_plan)
        print("alt: ",alt_plan)
        if isHigherSel == "True":
            higherSel = True
        else:
            higherSel = False

        print("isHigherSel: ",higherSel)

        actual_relation = alias_relation_dict[chosen_relation]

        if chosen_var in isVaryDict[actual_relation]:
            varying = False
        else:
            varying = True
        explanation_pos = explainPositionChange(ast.literal_eval(init_plan), ast.literal_eval(alt_plan), chosen_relation, varying, higherSel)
        explanation_join = explainJoinChange(ast.literal_eval(init_plan), ast.literal_eval(alt_plan), chosen_relation)
        if explanation_pos == None and explanation_join == None:
            return "No difference between plans for chosen relation."
        elif explanation_pos and explanation_join:
            return explanation_pos + explanation_join
        elif explanation_pos and explanation_join == None:
            return explanation_pos
        else:
            return explanation_join

@app.callback(
                Output("variable-vertices","children"),
                Output('alias-relation','children'),
                Output('isVarying','children'),
                Input('available-variables','value'),
                State('initial-plan-signatures','children'),

              )
def show_vertices(current_var,init_plan):

    if not current_var and not init_plan:
        raise PreventUpdate

    else:

        alias_names = [x[0] for x in ast.literal_eval(init_plan)]
        relation_names = [x[1] for x in ast.literal_eval(init_plan)]
        alias_relation = {}
        for i in range(len(alias_names)):
            alias_relation[alias_names[i]] = relation_names[i]

        isVaryingDict = {"lineitem": ["l_orderkey", "l_partkey", "l_suppkey", "l_linenumber", "l_quantity",
                        "l_extendedprice","l_discount", "l_tax", "l_shipdate", "l_receipt"],
                         "customer": ["c_custkey", "c_nationkey", "c_acctbal"],
                         "nation": ["n_nationkey", "n_regionkey"],
                         "orders": ["o_orderkey", "o_custkey", "o_totalprice", "o_orderdate", "o_shippingpriority"],
                         "part":["p_partkey", "p_size", "p_retailprice"],
                         "partsupp":["ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost"],
                         "region":["r_regionkey"],
                         "supplier":["supplier", "s_suppkey", "s_nationkey", "s_acctbal"]

                         }



        return [dcc.Dropdown(
            id = "all-vertice-variables",
            options=[{'label': i, 'value': i} for i in alias_names],
            placeholder="Choose a vertice for explanation...",
        )],json.dumps(alias_relation),json.dumps(isVaryingDict)



@app.callback(Output("selectivity-value-chosen","children"),
              Output("chosen-relation","children"),
              Output("chosen-date","children"),
              Input('intermediate-value','children'),
              Input('available-variables','value')
              )
def show_chosen_selectivity(query,value):
    if not query or not value:
        raise PreventUpdate
    else:
        conn = psycopg2.connect(database="TPC-H", user="root", password="password", host="localhost", port="5432")
        cur = conn.cursor()
        cur.execute("EXPLAIN " + query)
        rows = cur.fetchall()
        sel_arr = getSelectivityArray(rows, cur)
        selectivity = [x[2] for x in sel_arr[1] if x[1] == value][0]
        relation = [x[0] for x in sel_arr[1] if x[1] ==value][0]
        date = [x[3] for x in sel_arr[1] if x[1] == value][0]
        conn.close()
        return "Selectivity: "+str(round(selectivity*100,1))+"%", relation, date
@app.callback(
            Output('alt-plan', 'children'),
            Output('alt-plan-signatures','children'),
            Output("selectivity-value-alt","children"),
            Output("isHigherSel",'children'),
            Input('increase-sel', 'n_clicks'),
            Input('decrease-sel','n_clicks'),
            Input('intermediate-value','children'),
            Input("selectivity-value-chosen","children"),
            Input("chosen-relation", "children"),
            State('available-variables','value'),
            State('chosen-date','children'),
            State('initial-query-plan','children'),
            State('node-names','children')
            )
def selectivity(in_clicks,d_clicks,query,sel_value,relation,value,isDate,original_query_plan,original_node_names):
    global operations
    global counter
    global joinOnlyCounter
    global joins_global
    global signatures_global
    # joins_global = []
    # signatures_global = []
    if in_clicks and query:

        conn = psycopg2.connect(database="TPC-H", user="root", password="password", host="localhost", port="5432")
        cur = conn.cursor()
        # update new selectivity
        new_sel = float(sel_value[13:len(sel_value)-2])/100+0.1

        # while selectivity is not yet 100%, continue to increase
        while new_sel < 1:
            print(relation,value,new_sel,isDate)
            val = getConstant(cur,relation,value,new_sel,isDate)
            newQuery = getModifiedQuery(value,val,query,isDate)
            cur.execute("EXPLAIN (FORMAT JSON) " + newQuery)
            rows = cur.fetchall()
            new_query_plan = rows[0][0][0]
            counter = 1
            operations = list()
            joinOnlyCounter = 1
            signatures_global = []
            joins_global = []
            ####### Generate initial tree #######
            generateOpSeq(new_query_plan['Plan'], -1)
            # print("operations for new-query: ", operations)
            G, node_names = generateIGraph(operations)
            exploreChildren(new_query_plan['Plan'])
            print("new joins: ",joins_global)
            print("new signatures: ",signatures_global)


            if node_names!=original_node_names:

                ################ Plot graph ###################
                print("Creating alternate graph...")
                nr_vertices = G.vcount()
                print("Number of vertices: ", nr_vertices)
                v_label = list(map(str, range(nr_vertices)))
                lay = G.layout('rt')

                position = {k: lay[k] for k in range(nr_vertices)}
                Y = [lay[k][1] for k in range(nr_vertices)]
                M = max(Y)

                es = EdgeSeq(G)  # sequence of edges
                E = [e.tuple for e in G.es]  # list of edges

                L = len(position)
                Xn = [position[k][0] for k in range(L)]
                Yn = [2 * M - position[k][1] for k in range(L)]
                Xe = []
                Ye = []
                for edge in E:
                    Xe += [position[edge[0]][0], position[edge[1]][0], None]
                    Ye += [2 * M - position[edge[0]][1], 2 * M - position[edge[1]][1], None]

                labels = v_label

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=Xe,
                                         y=Ye,
                                         mode='lines',
                                         line=dict(color='rgb(210,210,210)', width=1),
                                         hoverinfo='none'
                                         ))
                fig.add_trace(go.Scatter(x=Xn,
                                         y=Yn,
                                         mode='markers',
                                         marker=dict(symbol='circle-dot',
                                                     size=18,
                                                     color='#6175c1',  # '#DB4551',
                                                     line=dict(color='rgb(50,50,50)', width=1)
                                                     ),
                                         text=node_names,
                                         hoverinfo='text',
                                         opacity=0.8
                                         ))

                axis = dict(showline=False,  # hide axis line, grid, ticklabels and  title
                            zeroline=False,
                            showgrid=False,
                            showticklabels=False,
                            )

                fig.update_layout(title='DBMS Alternate Plan',
                                  # annotations=make_annotations(position,),
                                  font_size=12,
                                  showlegend=False,
                                  xaxis=axis,
                                  yaxis=axis,
                                  margin=dict(l=40, r=40, b=85, t=100),
                                  hovermode='closest',
                                  plot_bgcolor='rgb(248,248,248)'
                                  )
                print("Alternate Graph created.")
                fig = go.FigureWidget(fig)
                conn.close()
                return dcc.Graph(id='tree',figure=fig),str(signatures_global),"Alt Selectivity: "+str(round(new_sel*100,1))+"%","True"
            new_sel +=0.1
    elif d_clicks and query:
        conn = psycopg2.connect(database="TPC-H", user="root", password="password", host="localhost", port="5432")
        cur = conn.cursor()
        # update new selectivity
        new_sel = float(sel_value[13:len(sel_value) - 2]) / 100 - 0.1

        # while selectivity is not yet 0%, continue to decrease
        while new_sel > 0:
            print(new_sel)
            val = getConstant(cur, relation, value, new_sel, isDate)
            newQuery = getModifiedQuery(value, val, query, isDate)
            cur.execute("EXPLAIN (FORMAT JSON) " + newQuery)
            rows = cur.fetchall()
            new_query_plan = rows[0][0][0]
            counter = 1
            operations = []
            joinOnlyCounter = 1
            signatures_global = []
            joins_global = []
            ####### Generate initial tree #######

            exploreChildren(new_query_plan['Plan'])
            generateOpSeq(new_query_plan['Plan'], -1)
            # print("operations for new-query: ", operations)
            G, node_names = generateIGraph(operations)
            if node_names != original_node_names:

                ################ Plot graph ###################
                print("Creating alternate graph...")
                nr_vertices = G.vcount()
                print("Number of vertices: ", nr_vertices)
                v_label = list(map(str, range(nr_vertices)))
                lay = G.layout('rt')

                position = {k: lay[k] for k in range(nr_vertices)}
                Y = [lay[k][1] for k in range(nr_vertices)]
                M = max(Y)

                es = EdgeSeq(G)  # sequence of edges
                E = [e.tuple for e in G.es]  # list of edges

                L = len(position)
                Xn = [position[k][0] for k in range(L)]
                Yn = [2 * M - position[k][1] for k in range(L)]
                Xe = []
                Ye = []
                for edge in E:
                    Xe += [position[edge[0]][0], position[edge[1]][0], None]
                    Ye += [2 * M - position[edge[0]][1], 2 * M - position[edge[1]][1], None]

                labels = v_label

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=Xe,
                                         y=Ye,
                                         mode='lines',
                                         line=dict(color='rgb(210,210,210)', width=1),
                                         hoverinfo='none'
                                         ))
                fig.add_trace(go.Scatter(x=Xn,
                                         y=Yn,
                                         mode='markers',
                                         marker=dict(symbol='circle-dot',
                                                     size=18,
                                                     color='#6175c1',  # '#DB4551',
                                                     line=dict(color='rgb(50,50,50)', width=1)
                                                     ),
                                         text=node_names,
                                         hoverinfo='text',
                                         opacity=0.8
                                         ))

                axis = dict(showline=False,  # hide axis line, grid, ticklabels and  title
                            zeroline=False,
                            showgrid=False,
                            showticklabels=False,
                            )

                fig.update_layout(title='DBMS Alternate Plan',
                                  # annotations=make_annotations(position,),
                                  font_size=12,
                                  showlegend=False,
                                  xaxis=axis,
                                  yaxis=axis,
                                  margin=dict(l=40, r=40, b=85, t=100),
                                  hovermode='closest',
                                  plot_bgcolor='rgb(248,248,248)'
                                  )
                print("Alternate Graph created.")
                fig = go.FigureWidget(fig)
                conn.close()
                return dcc.Graph(id='tree', figure=fig),str(signatures),"Alt Selectivity: "+str(round(new_sel*100,1))+"%","False"
            new_sel -= 0.1
        conn.close()
    return "","","",""
@app.callback(Output('upload-data', 'children'),
              Output('available-variables', 'options'),
              Output('intermediate-value','children'),
              Output('node-names','children'),
              Output('initial-query-plan','children'),
              Output('initial-plan-signatures','children'),
              Input('sql-file', 'contents'),
              )
def update_output(contents):
    # print("Processing SQL query...")
    global operations
    global counter
    global joinOnlyCounter
    global available_variables
    global joins_global
    global signatures_global
    available_variables = []
    counter= 1
    operations = []
    joinOnlyCounter = 1
    joins_global = []
    signatures_global = []

    if not contents:
        raise PreventUpdate
    else:
        ################ Processing query ###################
        temp = contents.split(",")
        base64_message = temp[1]
        base64_bytes = base64_message.encode('ascii')
        message_bytes = base64.b64decode(base64_bytes)
        file = message_bytes.decode('ascii')
        conn = psycopg2.connect(database="TPC-H", user="root", password="password", host="localhost", port="5432")
        cur = conn.cursor()
        cur.execute("EXPLAIN (FORMAT JSON) "+file)
        rows = cur.fetchall()
        query_plan = rows[0][0][0]

        ########## Explore Children ##########

        exploreChildren(query_plan['Plan'])
        print("Joins original:",joins_global)
        print("Signatures original: ",signatures_global)

        ####### Generate initial tree #######
        generateOpSeq(query_plan['Plan'], -1)
        print("operations: ",operations)
        G,node_names = generateIGraph(operations)

        ################ Plot graph ###################
        print("Creating graph...")
        nr_vertices = G.vcount()
        print("Number of vertices: ",nr_vertices)
        v_label = list(map(str, range(nr_vertices)))
        lay = G.layout('rt')

        position = {k: lay[k] for k in range(nr_vertices)}
        Y = [lay[k][1] for k in range(nr_vertices)]
        M = max(Y)

        es = EdgeSeq(G)  # sequence of edges
        E = [e.tuple for e in G.es]  # list of edges

        L = len(position)
        Xn = [position[k][0] for k in range(L)]
        Yn = [2 * M - position[k][1] for k in range(L)]
        Xe = []
        Ye = []
        for edge in E:
            Xe += [position[edge[0]][0], position[edge[1]][0], None]
            Ye += [2 * M - position[edge[0]][1], 2 * M - position[edge[1]][1], None]

        labels = v_label


        fig = go.Figure()
        fig.add_trace(go.Scatter(x=Xe,
                                 y=Ye,
                                 mode='lines',
                                 line=dict(color='rgb(210,210,210)', width=1),
                                 hoverinfo='none'
                                 ))
        fig.add_trace(go.Scatter(x=Xn,
                                 y=Yn,
                                 mode='markers',
                                 marker=dict(symbol='circle-dot',
                                             size=18,
                                             color='#6175c1',  # '#DB4551',
                                             line=dict(color='rgb(50,50,50)', width=1)
                                             ),
                                 text=node_names,
                                 hoverinfo='text',
                                 opacity=0.8
                                 ))

        axis = dict(showline=False,  # hide axis line, grid, ticklabels and  title
                    zeroline=False,
                    showgrid=False,
                    showticklabels=False,
                    )

        fig.update_layout(title='DBMS Selected Plan',
                          # annotations=make_annotations(position,),
                          font_size=12,
                          showlegend=False,
                          xaxis=axis,
                          yaxis=axis,
                          margin=dict(l=40, r=40, b=85, t=100),
                          hovermode='closest',
                          plot_bgcolor='rgb(248,248,248)'
                          )
        print("Graph created.")
        fig = go.FigureWidget(fig)


        ################ Selectivity #####################
        cur.execute("EXPLAIN " + file)
        rows = cur.fetchall()
        sel_arr = getSelectivityArray(rows,cur)
        print("selectivity array: ",sel_arr)
        available_variables = [x[1] for x in sel_arr[0]]
        # all_relations = [x[0] for x in sel_arr[0]]
        # corr_relations = {}
        # for i in range(len(available_variables)):
        #     corr_relations[all_relations[i]] = available_variables[i]
        # selectivity_variables = [x[2] for x in sel_arr[1]]

        conn.close()

        return dcc.Graph(id='tree',figure=fig),[{'label': i, 'value': i} for i in available_variables],file,node_names,json.dumps(query_plan),str(signatures_global)

app.css.append_css({
    'external_url': 'https://codepen.io/chriddyp/pen/bWLwgP.css'
})

if __name__ == '__main__':
    app.run_server()
