#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on March 2019
@author: Thomas Bonald <bonald@enst.fr>
@author: Quentin Lutz <qlutz@enst.fr>
"""

import unittest

import numpy as np
from scipy import sparse

from sknetwork import is_numba_available
from sknetwork.hierarchy import Paris, BiParis, straight_cut, balanced_cut
from sknetwork.data import karate_club, painters, movie_actor


# noinspection PyMissingOrEmptyDocstring
class TestParis(unittest.TestCase):

    def setUp(self):
        self.paris = [Paris(engine='python')]
        self.biparis = [BiParis(engine='python')]
        if is_numba_available:
            self.paris.append(Paris(engine='numba'))
            self.biparis.append(BiParis(engine='numba'))
        else:
            with self.assertRaises(ValueError):
                Paris(engine='numba')

    def test_undirected(self):
        adjacency = karate_club()
        for paris in self.paris:
            dendrogram = paris.fit_transform(adjacency)
            n = adjacency.shape[0]
            self.assertEqual(dendrogram.shape, (n - 1, 4))
            labels = straight_cut(dendrogram, sorted_clusters=True)
            self.assertEqual(len(set(labels)), 2)
            labels = balanced_cut(dendrogram, max_cluster_size=10)
            self.assertEqual(len(set(labels)), 5)

    def test_directed(self):
        adjacency = painters()
        for paris in self.paris:
            dendrogram = paris.fit_transform(adjacency)
            n = adjacency.shape[0]
            self.assertEqual(dendrogram.shape, (n - 1, 4))
            labels = straight_cut(dendrogram, sorted_clusters=True)
            self.assertEqual(len(set(labels)), 2)
            labels = balanced_cut(dendrogram, max_cluster_size=10)
            self.assertEqual(len(set(labels)), 2)

    def test_bipartite(self):
        biadjacency = movie_actor()
        for biparis in self.biparis:
            biparis.fit(biadjacency)
            n1, n2 = biadjacency.shape
            self.assertEqual(biparis.dendrogram_.shape, (n1 - 1, 4))
            self.assertEqual(biparis.dendrogram_row_.shape, (n1 - 1, 4))
            self.assertEqual(biparis.dendrogram_col_.shape, (n2 - 1, 4))
            self.assertEqual(biparis.dendrogram_full_.shape, (n1 + n2 - 1, 4))

    def test_disconnected(self):
        adjacency = np.eye(10)
        paris = Paris(engine='python')
        dendrogram = paris.fit_transform(adjacency)
        self.assertEqual(dendrogram.shape, (9, 4))

    def test_options(self):
        paris = Paris(weights='uniform')
        adjacency = karate_club()
        dendrogram = paris.fit_transform(adjacency)
        n = adjacency.shape[0]
        self.assertEqual(dendrogram.shape, (n - 1, 4))

    # noinspection PyTypeChecker
    def test_unknown_types(self):
        with self.assertRaises(TypeError):
            for paris in self.paris:
                paris.fit(sparse.identity(1))
