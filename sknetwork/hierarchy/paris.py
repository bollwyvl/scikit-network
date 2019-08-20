#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on March 2019
@author: Thomas Bonald <bonald@enst.fr>
@author: Bertrand Charpentier <bertrand.charpentier@live.fr>
@author: Quentin Lutz <qlutz@enst.fr>
"""

from typing import Union

import numpy as np
from scipy import sparse

from sknetwork import njit, types, TypedDict
from sknetwork.utils.adjacency_formats import set_adjacency_weights
from sknetwork.utils.algorithm_base_class import Algorithm
from sknetwork.utils.checks import check_engine, check_format


class AggregateGraph:
    """
    A class of graphs suitable for aggregation. Each node represents a cluster.

    Parameters
    ----------
    adjacency :
        Adjacency matrix of the graph.
    out_weights :
        Out-weights (summing to 1).
    in_weights :
        In-weights (summing to 1).

    Attributes
    ----------
    neighbors : dict[dict]
        Dictionary of dictionary of edge weights.
    next_cluster : int
        Index of the next cluster (resulting from aggregation).
    cluster_sizes : dict
        Dictionary of cluster sizes.
    cluster_out_weights : dict
        Dictionary of cluster out-weights.
    cluster_in_weights : dict
        Dictionary of cluster in-weights.
    """

    def __init__(self, adjacency: sparse.csr_matrix, out_weights: np.ndarray, in_weights: np.ndarray):
        n = adjacency.shape[0]
        total_weight = adjacency.data.sum()

        self.next_cluster = n
        self.neighbors = {}
        for node in range(n):
            # normalize so that the total weight is equal to 1
            # remove self-loops
            self.neighbors[node] = {adjacency.indices[i]: 2 * adjacency.data[i] / total_weight for i in
                                    range(adjacency.indptr[node], adjacency.indptr[node + 1])
                                    if adjacency.indices[i] != node}
        self.cluster_sizes = {node: 1 for node in range(n)}
        self.cluster_out_weights = {node: out_weights[node] for node in range(n)}
        self.cluster_in_weights = {node: in_weights[node] for node in range(n)}

    def similarity(self, node1: int, node2: int) -> float:
        """Similarity of two nodes.

        Parameters
        ----------
        node1, node2 :
            Nodes.

        Returns
        -------
        sim: float
            Similarity.
        """
        sim = -float("inf")
        den = self.cluster_out_weights[node1] * self.cluster_in_weights[node2] + self.cluster_out_weights[node2] * \
            self.cluster_in_weights[node1]
        if den > 0:
            sim = 2 * self.neighbors[node1][node2] / den
        return sim

    def merge(self, node1: int, node2: int) -> 'AggregateGraph':
        """Merges two nodes.

        Parameters
        ----------
        node1, node2 :
            The two nodes to merge.

        Returns
        -------
        self: :class:`AggregateGraph`
            The aggregate grate (without self-loop).
        """
        new_node = self.next_cluster
        self.neighbors[new_node] = {}
        common_neighbors = set(self.neighbors[node1]) & set(self.neighbors[node2]) - {node1, node2}
        for node in common_neighbors:
            self.neighbors[new_node][node] = self.neighbors[node1][node] + self.neighbors[node2][node]
            self.neighbors[node][new_node] = self.neighbors[node].pop(node1) + self.neighbors[node].pop(node2)
        node1_neighbors = set(self.neighbors[node1]) - set(self.neighbors[node2]) - {node2}
        for node in node1_neighbors:
            self.neighbors[new_node][node] = self.neighbors[node1][node]
            self.neighbors[node][new_node] = self.neighbors[node].pop(node1)
        node2_neighbors = set(self.neighbors[node2]) - set(self.neighbors[node1]) - {node1}
        for node in node2_neighbors:
            self.neighbors[new_node][node] = self.neighbors[node2][node]
            self.neighbors[node][new_node] = self.neighbors[node].pop(node2)
        del self.neighbors[node1]
        del self.neighbors[node2]
        self.cluster_sizes[new_node] = self.cluster_sizes.pop(node1) + self.cluster_sizes.pop(node2)
        self.cluster_out_weights[new_node] = self.cluster_out_weights.pop(node1) + self.cluster_out_weights.pop(node2)
        self.cluster_in_weights[new_node] = self.cluster_in_weights.pop(node1) + self.cluster_in_weights.pop(node2)
        self.next_cluster += 1
        return self


def reorder_dendrogram(dendrogram: np.ndarray) -> np.ndarray:
    """
    Get the dendrogram in increasing order of height.

    Parameters
    ----------
    dendrogram:
        Original dendrogram.

    Returns
    -------
    dendrogram: np.ndarray
        Reordered dendrogram.
    """
    n_nodes = np.shape(dendrogram)[0] + 1
    order = np.zeros((2, n_nodes - 1), float)
    order[0] = np.arange(n_nodes - 1)
    order[1] = np.array(dendrogram)[:, 2]
    index = np.lexsort(order)
    node_index = np.arange(2 * n_nodes - 1)
    for t in range(n_nodes - 1):
        node_index[n_nodes + index[t]] = n_nodes + t
    return np.array([[node_index[int(dendrogram[t][0])], node_index[int(dendrogram[t][1])],
                      dendrogram[t][2], dendrogram[t][3]] for t in range(n_nodes - 1)])[index, :]


@njit
def ints2int(first: np.int32, second: np.int32):
    """Merge two int32 into one single int64

    Parameters
    ----------
    first:
        An int32 making up the first 32 bits of the result
    second:
        An int32 making up the last 32 bits of the result

    Returns
    -------
    result: np.int64
        An int64.
    """
    return (first << 32) | second


@njit
def fit_core(n: int, out_weights: np.ndarray, in_weights: np.ndarray, data: np.ndarray,
             indices: np.ndarray, indptr: np.ndarray):
    """

    Parameters
    ----------
    n:
        Number of nodes.
    out_weights :
        Out-weights (summing to 1).
    in_weights :
        In-weights (summing to 1).
    data:
        CSR format data array of the normalized adjacency matrix.
    indices:
        CSR format index array of the normalized adjacency matrix.
    indptr:
        CSR format index pointer array of the normalized adjacency matrix.

    Returns
    -------
    dendrogram:
        Dendrogram.
    """
    maxfloat = 1.7976931348623157e+308
    total_weight = data.sum()
    next_cluster = n
    graph = TypedDict.empty(
        key_type=types.int64,
        value_type=types.float64,
    )

    cluster_sizes = {}
    cluster_out_weights = {}
    cluster_in_weights = {}
    neighbors = [[types.int32(-1)] for _ in range(n)]
    for node in range(n):
        node = types.int32(node)
        # normalize so that the total weight is equal to 1
        # remove self-loops
        for i in range(indptr[types.int32(node)], indptr[node + 1]):
            if i == indptr[types.int32(node)]:
                neighbors[node][0] = indices[i]
            else:
                neighbors[node].append(indices[i])
            if indices[i] != node:
                graph[ints2int(node, indices[i])] = data[i] / total_weight
        if node in neighbors[node]:
            neighbors[node].remove(node)
        cluster_sizes[node] = 1
        cluster_out_weights[node] = out_weights[types.int32(node)]
        cluster_in_weights[node] = in_weights[types.int32(node)]

    connected_components = []
    dendrogram = []

    while len(cluster_sizes) > 0:
        node = None
        for node in cluster_sizes:
            break
        chain = [node]
        while chain:
            node = chain.pop()
            if neighbors[node][0] != -1:
                max_sim = -maxfloat
                nearest_neighbor = None
                for neighbor in neighbors[node]:
                    sim = 0
                    den = cluster_out_weights[node] * cluster_in_weights[neighbor] + cluster_out_weights[neighbor] * \
                        cluster_in_weights[node]
                    if den > 0:
                        sim = 2 * graph[ints2int(node, neighbor)] / den
                    if sim > max_sim:
                        nearest_neighbor = neighbor
                        max_sim = sim
                    elif sim == max_sim:
                        nearest_neighbor = min(neighbor, nearest_neighbor)
                if chain:
                    nearest_neighbor_last = chain.pop()
                    if nearest_neighbor_last == nearest_neighbor:
                        dendrogram.append([node, nearest_neighbor, 1. / max_sim,
                                           cluster_sizes[node]
                                           + cluster_sizes[nearest_neighbor]])
                        # merge
                        new_node = types.int32(next_cluster)
                        neighbors.append([types.int32(-1)])
                        common_neighbors = set(neighbors[node]) & set(neighbors[nearest_neighbor]) - {types.int32(node),
                                                                                                      types.int32(
                                                                                                      nearest_neighbor)}
                        for curr_node in common_neighbors:
                            graph[ints2int(new_node, curr_node)] = graph[ints2int(node, curr_node)] + graph[
                                ints2int(nearest_neighbor, curr_node)]
                            graph[ints2int(curr_node, new_node)] = graph.pop(ints2int(curr_node, node)) + graph.pop(
                                ints2int(curr_node, nearest_neighbor))
                            if neighbors[new_node][0] != -1:
                                neighbors[new_node].append(curr_node)
                            else:
                                neighbors[new_node][0] = curr_node
                            neighbors[curr_node].append(new_node)
                            neighbors[curr_node].remove(node)
                            neighbors[curr_node].remove(nearest_neighbor)
                        node_neighbors = set(neighbors[node]) - set(neighbors[nearest_neighbor]) - {types.int32(
                            nearest_neighbor)}
                        for curr_node in node_neighbors:
                            graph[ints2int(new_node, curr_node)] = graph[ints2int(node, curr_node)]
                            graph[ints2int(curr_node, new_node)] = graph.pop(ints2int(curr_node, node))
                            if neighbors[new_node][0] != -1:
                                neighbors[new_node].append(curr_node)
                            else:
                                neighbors[new_node][0] = curr_node
                            neighbors[curr_node].append(new_node)
                            neighbors[curr_node].remove(node)
                        nearest_neighbor_neighbors = set(neighbors[nearest_neighbor]) - set(neighbors[node]) - {
                            types.int32(node)}
                        for curr_node in nearest_neighbor_neighbors:
                            graph[ints2int(new_node, curr_node)] = graph[ints2int(nearest_neighbor, curr_node)]
                            graph[ints2int(curr_node, new_node)] = graph.pop(ints2int(curr_node, nearest_neighbor))
                            if neighbors[new_node][0] != -1:
                                neighbors[new_node].append(curr_node)
                            else:
                                neighbors[new_node][0] = curr_node
                            neighbors[curr_node].append(new_node)
                            neighbors[curr_node].remove(nearest_neighbor)
                        neighbors[node] = [types.int32(-1)]
                        neighbors[nearest_neighbor] = [types.int32(-1)]
                        cluster_sizes[new_node] = cluster_sizes.pop(node) + cluster_sizes.pop(nearest_neighbor)
                        cluster_out_weights[new_node] = cluster_out_weights.pop(node) + \
                            cluster_out_weights.pop(nearest_neighbor)
                        cluster_in_weights[new_node] = cluster_in_weights.pop(node) + \
                            cluster_in_weights.pop(nearest_neighbor)
                        next_cluster += 1
                    else:
                        chain.append(nearest_neighbor_last)
                        chain.append(node)
                        chain.append(nearest_neighbor)
                else:
                    chain.append(node)
                    chain.append(nearest_neighbor)
            else:
                connected_components.append((node, cluster_sizes[node]))
                del cluster_sizes[node]

    node, cluster_size = connected_components.pop()
    for next_node, next_cluster_size in connected_components:
        cluster_size += next_cluster_size
        dendrogram.append([node, next_node, maxfloat, cluster_size])
        node = next_cluster
        next_cluster += 1
    return dendrogram


class Paris(Algorithm):
    """
    Agglomerative clustering algorithm that performs greedy merge of clusters based on their similarity.

    The similarity between clusters i,j is :math:`\\dfrac{A_{ij}}{w_i w_j}` where

    * :math:`A_{ij}` is the weight of edge i,j in the aggregate graph,
    * :math:`w_{i}` is the weight of cluster i,
    * :math:`w_{j}` is the weight of cluster j.

    Parameters
    ----------
    engine : str
        ``'default'``, ``'python'`` or ``'numba'``. If ``'default'``, tests if numba is available.

    Attributes
    ----------
    dendrogram_ : numpy array of shape (total number of nodes - 1, 4)
        Dendrogram.

    Examples
    --------
    >>> from sknetwork.toy_graphs import house
    >>> adjacency = house()
    >>> paris = Paris('python')
    >>> paris.fit(adjacency).dendrogram_
    array([[3.        , 2.        , 0.16666667, 2.        ],
           [1.        , 0.        , 0.25      , 2.        ],
           [6.        , 4.        , 0.3125    , 3.        ],
           [7.        , 5.        , 0.66666667, 5.        ]])

    Notes
    -----
    Each row of the dendrogram = :math:`i, j`, height, size of cluster :math:`i + j`.


    See Also
    --------
    scipy.cluster.hierarchy.dendrogram

    References
    ----------
    T. Bonald, B. Charpentier, A. Galland, A. Hollocou (2018).
    Hierarchical Graph Clustering using Node Pair Sampling.
    Workshop on Mining and Learning with Graphs.
    https://arxiv.org/abs/1806.01664

    """

    def __init__(self, engine: str = 'default'):
        self.dendrogram_ = None
        self.engine = check_engine(engine)

    def fit(self, adjacency: sparse.csr_matrix, weights: Union[str, np.ndarray] = 'degree',
            secondary_weights: Union[None, str, np.ndarray] = None, force_undirected: bool = False,
            force_biadjacency: bool = False, reorder: bool = True) -> 'Paris':
        """
        Agglomerative clustering using the nearest neighbor chain.

        Parameters
        ----------
        adjacency :
            Adjacency or biadjacency matrix of the graph.
        weights :
            Weights of nodes.
            ``'degree'`` (default), ``'uniform'`` or custom weights.
        secondary_weights :
            Weights of secondary nodes (for bipartite graphs).
            ``None`` (default), ``'degree'``, ``'uniform'`` or custom weights.
            If ``None``, taken equal to weights.
        force_undirected : bool (default= ``False``)
            If ``True``, consider the graph as undirected.
        force_biadjacency : bool (default= ``False``)
            If ``True``, force the input matrix to be considered as a biadjacency matrix.
        reorder :
            If True, reorder the dendrogram in increasing order of heights.

        Returns
        -------
        self: :class: 'Paris'
        """
        adjacency = check_format(adjacency)
        adjacency, out_weights, in_weights = set_adjacency_weights(adjacency, weights, secondary_weights,
                                                                   force_undirected, force_biadjacency)
        n = adjacency.shape[0]
        if n <= 1:
            raise ValueError('The graph must contain at least two nodes.')

        if self.engine == 'python':
            aggregate_graph = AggregateGraph(adjacency + adjacency.T, out_weights, in_weights)

            connected_components = []
            dendrogram = []

            while len(aggregate_graph.cluster_sizes) > 0:
                node = None
                for node in aggregate_graph.cluster_sizes:
                    break
                chain = [node]
                while chain:
                    node = chain.pop()
                    if aggregate_graph.neighbors[node]:
                        max_sim = -float("inf")
                        nearest_neighbor = None
                        for neighbor in aggregate_graph.neighbors[node]:
                            sim = aggregate_graph.similarity(node, neighbor)
                            if sim > max_sim:
                                nearest_neighbor = neighbor
                                max_sim = sim
                            elif sim == max_sim:
                                nearest_neighbor = min(neighbor, nearest_neighbor)
                        if chain:
                            nearest_neighbor_last = chain.pop()
                            if nearest_neighbor_last == nearest_neighbor:
                                dendrogram.append([node, nearest_neighbor, 1. / max_sim,
                                                   aggregate_graph.cluster_sizes[node]
                                                   + aggregate_graph.cluster_sizes[nearest_neighbor]])
                                aggregate_graph.merge(node, nearest_neighbor)
                            else:
                                chain.append(nearest_neighbor_last)
                                chain.append(node)
                                chain.append(nearest_neighbor)
                        else:
                            chain.append(node)
                            chain.append(nearest_neighbor)
                    else:
                        connected_components.append((node, aggregate_graph.cluster_sizes[node]))
                        del aggregate_graph.cluster_sizes[node]

            node, cluster_size = connected_components.pop()
            for next_node, next_cluster_size in connected_components:
                cluster_size += next_cluster_size
                dendrogram.append([node, next_node, float("inf"), cluster_size])
                node = aggregate_graph.next_cluster
                aggregate_graph.next_cluster += 1

            dendrogram = np.array(dendrogram)
            if reorder:
                dendrogram = reorder_dendrogram(dendrogram)

            self.dendrogram_ = dendrogram
            return self

        elif self.engine == 'numba':

            n = np.int32(adjacency.shape[0])
            sym_adjacency = adjacency + adjacency.T
            indices, indptr, data = sym_adjacency.indices, sym_adjacency.indptr, sym_adjacency.data

            dendrogram = fit_core(n, out_weights, in_weights, data, indices, indptr)
            dendrogram = np.array(dendrogram)
            if reorder:
                dendrogram = reorder_dendrogram(dendrogram)

            self.dendrogram_ = dendrogram
            return self

        else:
            raise ValueError('Unknown engine.')
