"""
The wntr.metrics.hydraulic module contains hydraulic metrics.
"""
import wntr.network
from wntr.network.graph import _all_simple_paths
#from wntr.metrics.misc import _average_attribute
import numpy as np
import pandas as pd
import networkx as nx
import math
from collections import Counter

import logging

logger = logging.getLogger(__name__)

def fdv(node_results, average_times=False, average_nodes=False):
    """
    Compute fraction delivered volume (FDV), equations modified from [1].
    The metric can be averaged over times and/or nodes.

    Parameters
    ----------
    node_results : pd.Panel
        A pandas Panel containing node results.
        Items axis = attributes, Major axis = times, Minor axis = node names
        FDV uses 'expected demand' and 'demand' attrbutes.

    average_times : bool (default = False)
        Flag to determine if calculations are to be averaged over each time
        step. If false, FDV calculations will be performed for each time step.
        If true, FDV calculations will be averaged over all time steps.

    average_nodes : bool (default = False)
        Flag to determine if calculations are to be averaged over each node.
        If false, FDV calculations will be performed for each node. If true, FDV
        calculations will be averaged over all nodes.

    Returns
    -------
    fdv : pd.DataFrame, pd.Series, or scalar (depending on node and time averaging)
        Fraction of delivered volume

    References
    ----------
    [1] Ostfeld A, Kogan D, Shamir U. (2002). Reliability simulation of water
    distribution systems - single and multiquality, Urban Water, 4, 53-61
    """

    exp_demand = _average_attribute(node_results['expected_demand'], average_times, average_nodes)
    act_received = _average_attribute(node_results['demand'], average_times, average_nodes)

    # Calculate FDV
    fdv = act_received / exp_demand

    # Replace NaNs (generated by nodes with 0 demand)
    try:
        fdv = fdv.fillna(1)
    except:
        if exp_demand == 0:
            fdv = 1

    return fdv

def fdd(node_results, Dstar, average_times=False, average_nodes=False):
    """
    Compute fraction delivered demand (FDD), equations modified from [1].
    The metric can be averaged over times and/or nodes.

    Parameters
    ----------
    node_results : pd.Panel
        A pandas Panel containing node results.
        Items axis = attributes, Major axis = times, Minor axis = node names
        FDD uses 'expected demand' and 'demand' attrbutes.

    Dstar : float
        Threshold demand factor

    average_times : bool (default = False)
        Flag to determine if calculations are to be averaged over each time
        step. If false, FDV calculations will be performed for each time step.
        If true, FDV calculations will be averaged over all time steps.

    average_nodes : bool (default = False)
        Flag to determine if calculations are to be averaged over each node.
        If false, FDV calculations will be performed for each node. If true, FDV
        calculations will be averaged over all nodes.

    Returns
    -------
    fdd : pd.DataFrame, pd.Series, or scalar (depending on node and time averaging)
        Fraction of delivered demand

    References
    ----------
    [1] Ostfeld A, Kogan D, Shamir U. (2002). Reliability simulation of water
    distribution systems - single and multiquality, Urban Water, 4, 53-61
    """

    fdv_metric = fdv(node_results, average_times, average_nodes)

    # Calculate FDD
    fdd = (fdv_metric >= Dstar)+0

    return fdd

def _average_attribute(attribute, average_times, average_nodes):
     # Average for all times and nodes
    if average_times==False and average_nodes==False:
        pass

    # Average for all nodes (averaged over all times)
    if average_times==True and average_nodes==False:
        attribute = attribute.sum(axis=0)

    # Average for all time (averaged over all nodes)
    if average_times==False and average_nodes==True:
        attribute = attribute.sum(axis=1)

    # Average for scenario (averaged over all times and nodes)
    if average_times==True and average_nodes==True:
        attribute = attribute.sum().sum()

    return attribute

def todini(node_results, link_results, wn, Pstar):
    """
    Compute Todini index, equations from [1].

    The Todini index is related to the capability of a system to overcome
    failures while still meeting demands and pressures at the nodes. The
    Todini index defines resilience at a specific time as a measure of surplus
    power at each node and measures relative energy redundancy.

    Parameters
    ----------
    node_results : pd.Panel
        A pandas Panel containing node results.
        Items axis = attributes, Major axis = times, Minor axis = node names
        todini index uses 'head', 'pressure', and 'demand' attrbutes.

    link_results : pd.Panel
        A pandas Panel containing link results.
        Items axis = attributes, Major axis = times, Minor axis = link names
        todini index uses the 'flowrate' attrbute.

    wn : Water Network Model
        A water network model.  The water network model is needed to find the start and end node to each pump.

    Pstar : float
        Pressure threshold.

    Returns
    -------
    todini_index : pd.Series
        Time-series of Todini indexes

    References
    -----------
    [1] Todini E. (2000). Looped water distribution networks design using a
    resilience index based heuristic approach. Urban Water, 2(2), 115-122.
    """

    POut = {}
    PExp = {}
    PInRes = {}
    PInPump = {}

    for name, node in wn.nodes(wntr.network.Junction):
        h = np.array(node_results['head'].loc[:,name]) # m
        p = np.array(node_results['pressure'].loc[:,name])
        e = h - p # m
        q = np.array(node_results['demand'].loc[:,name]) # m3/s
        POut[name] = q*h
        PExp[name] = q*(Pstar+e)

    for name, node in wn.nodes(wntr.network.Reservoir):
        H = np.array(node_results['head'].loc[:,name]) # m
        Q = np.array(node_results['demand'].loc[:,name]) # m3/s
        PInRes[name] = -Q*H # switch sign on Q.

    for name, link in wn.links(wntr.network.Pump):
        start_node = link._start_node_name
        end_node = link._end_node_name
        h_start = np.array(node_results['head'].loc[:,start_node]) # (m)
        h_end = np.array(node_results['head'].loc[:,end_node]) # (m)
        h = h_start - h_end # (m)
        q = np.array(link_results['flowrate'].loc[:,name]) # (m^3/s)
        PInPump[name] = q*(abs(h)) # assumes that pumps always add energy to the system

    todini_index = (sum(POut.values()) - sum(PExp.values()))/  \
        (sum(PInRes.values()) + sum(PInPump.values()) - sum(PExp.values()))

    todini_index = pd.Series(data = todini_index.tolist(), index = node_results['head'].index)

    return todini_index

def entropy(G, sources=None, sinks=None):
    """
    Compute entropy, equations from [1].

    Entropy is a measure of uncertainty in a random variable.
    In a water distribution network model, the random variable is
    flow in the pipes and entropy can be used to measure alternate flow paths
    when a network component fails.  A network that carries maximum entropy
    flow is considered reliable with multiple alternate paths.

    Parameters
    ----------
    G : NetworkX or WNTR graph
        Entropy is computed using a directed graph based on pipe flow direction.
        The 'weight' of each link is equal to the flow rate.

    sources : list of strings, optional (default = all reservoirs)
        List of node names to use as sources.

    sinks : list of strings, optional (default = all nodes)
        List of node names to use as sinks.

    Returns
    -------
    S : dict
        Node entropy, {node name: entropy value}

    Shat : float
        System entropy

    References
    -----------
    [1] Awumah K, Goulter I, Bhatt SK. (1990). Assessment of reliability in
    water distribution networks using entropy based measures. Stochastic
    Hydrology and Hydraulics, 4(4), 309-320
    """

    if G.is_directed() == False:
        return

    if sources is None:
        sources = [key for key,value in nx.get_node_attributes(G,'type').items() if value == 'reservoir' ]

    if sinks is None:
        sinks = G.nodes()

    S = {}
    Q = {}
    for nodej in sinks:
        if nodej in sources:
            S[nodej] = 0 # nodej is the source
            continue

        sp = [] # simple path
        if G.node[nodej]['type']  == 'junction':
            for source in sources:
                if nx.has_path(G, source, nodej):
                    simple_paths = _all_simple_paths(G,source,target=nodej)
                    sp = sp + ([p for p in simple_paths])
                    # all_simple_paths was modified to check 'has_path' in the
                    # loop, but this is still slow for large networks
                    # what if the network was skeletonized based on series pipes
                    # that have the same flow direction?
                    # what about duplicating paths that have pipes in series?
                #print j, nodeid, len(sp)

        if len(sp) == 0:
            S[nodej] = np.nan # nodej is not connected to any sources
            continue

        sp = np.array(sp)

        # Uj = set of nodes on the upstream ends of links incident on node j
        Uj = G.predecessors(nodej)
        # qij = flow in link from node i to node j
        qij = []
        # aij = number of equivalnet independent paths through the link from node i to node j
        aij = []
        for nodei in Uj:
            mask = np.array([nodei in path for path in sp])
            # NDij = number of paths through the link from node i to node j
            NDij = sum(mask)
            if NDij == 0:
                continue
            temp = sp[mask]
            # MDij = links in the NDij path
            MDij = [(t[idx],t[idx+1]) for t in temp for idx in range(len(t)-1)]

            flow = 0
            for link in G[nodei][nodej].keys():
                flow = flow + G[nodei][nodej][link]['weight']
            qij.append(flow)

            # dk = degree of link k in MDij
            dk = Counter()
            for elem in MDij:
                # divide by the numnber of links between two nodes
                dk[elem] += 1/len(G[elem[0]][elem[1]].keys())
            V = np.array(list(dk.values()))
            aij.append(NDij*(1-float(sum(V - 1))/sum(V)))

        Q[nodej] = sum(qij) # Total flow into node j

        # Equation 7
        S[nodej] = 0
        for idx in range(len(qij)):
            if qij[idx]/Q[nodej] > 0:
                S[nodej] = S[nodej] - \
                    qij[idx]/Q[nodej]*math.log(qij[idx]/Q[nodej]) + \
                    qij[idx]/Q[nodej]*math.log(aij[idx])

    Q0 = sum(nx.get_edge_attributes(G, 'weight').values())

    # Equation 3
    Shat = 0
    for nodej in sinks:
        if not np.isnan(S[nodej]):
            if nodej not in sources:
                if Q[nodej]/Q0 > 0:
                    Shat = Shat + \
                        (Q[nodej]*S[nodej])/Q0 - \
                        Q[nodej]/Q0*math.log(Q[nodej]/Q0)

    return [S, Shat]
