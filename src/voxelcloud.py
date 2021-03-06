# -*- coding: utf-8 -*-

import random
import time
import numpy as np
from tqdm import tqdm
from scipy import sparse
from sklearn.neighbors import KDTree
from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture
from sklearn.cluster import SpectralClustering
from sklearn.preprocessing import normalize
from sklearn.decomposition import PCA

from descriptors import local_PCA, features_from_PCA
from RANSAC import RANSAC, in_plane

class VoxelCloud:
    def __init__(self, pointcloud, max_voxel_size = 0.3, min_voxel_length = 4, threshold_grow = 1.5, method = "regular", seed = 42):
        """
            Builds a voxel cloud from a PointCloud object
    
            Parameters
            ----------
            pointcloud : PointCloud object
            max_voxel_size : float, optional
                Maximum size of the bounding box of the voxel (may be exceeded when 
                reassociating small voxels to bigger ones). The default is 0.3.
            min_voxel_length : int, optional
                Voxels under this size will be reassociated to bigger ones. The default is 4.
            threshold_grow : float, optional
                Small voxels under min_voxel_length will be associated to the closest bigger
                one if their distance is smaller than threshold_grow * big_voxel_size. The default is 1.5,
                which means that big voxels may grow of 50%
            seed : TYPE, optional
                For reproducibility. The default is 42.
        """
        self.pointcloud = pointcloud
        
        self.voxels = []
        self.too_small_voxels = []
        self.removed_voxels = []
        self.neighbourhood_classifier = None
        
        if method == "regular":
            self.compute_voxels_regular_grid(max_voxel_size, min_voxel_length)
        else:
            self.compute_voxels(max_voxel_size, min_voxel_length, seed)
        
        # Now removes voxels whose size is under min_voxel_length
        self.postprocess_small_voxels(threshold_grow)
        
        # Initializes features
        nb_voxels = len(self)
        self.features = {}
        self.features['nb_points'] = np.zeros(nb_voxels)
        self.features['geometric_center'] = np.zeros((nb_voxels, 3))
        self.features['size'] = np.zeros((nb_voxels, 3))
        self.features['normal'] = np.zeros((nb_voxels, 3))
        self.features['barycenter'] = np.zeros((nb_voxels, 3))
        self.features['mean_intensity'] = np.nan * np.ones(nb_voxels)
        self.features['var_intensity'] = np.nan * np.ones(nb_voxels)
        self.features['mean_color'] = np.nan * np.ones((nb_voxels, 3))
        self.features['var_color'] = np.nan * np.ones((nb_voxels, 3))
        self.features['verticality'] = np.nan * np.ones(nb_voxels)
        self.features['linearity'] = np.nan * np.ones(nb_voxels)
        self.features['planarity'] = np.nan * np.ones(nb_voxels)
        self.features['sphericity'] = np.nan * np.ones(nb_voxels)
        self.features['majority_label'] = np.nan * np.ones(nb_voxels)
        self.features['certainty_label'] = np.nan * np.ones(nb_voxels)
        self.compute_features()
        
        # Initializes a kd-tree with geometric centers for further searches
        self.kdt = KDTree(self.features['geometric_center'])
    
    def compute_voxels_regular_grid(self, max_voxel_size, min_voxel_length):
        """
            Computes the voxels by regularly sampling the point space
        """
        # TODO max_voxel_size dépendant de la direction ? 3x1 ?
        pts = self.pointcloud.get_coordinates()
        mini = np.min(pts, axis = 0)
        maxi = np.max(pts, axis = 0)
        
        centers = []
        # Traitement des z les uns après les autres pour éviter de remplir la RAM
        print("Scanning point cloud at every height to find populated cells")
        for z in tqdm(np.arange(mini[2] + max_voxel_size / 2, maxi[2], max_voxel_size)):
            # print(f"z={z:.2f}")
            centers_xy = np.stack(np.meshgrid(np.arange(mini[0] + max_voxel_size / 2, maxi[0], max_voxel_size), np.arange(mini[1] + max_voxel_size / 2, maxi[1], max_voxel_size)), axis=2).reshape((-1,2))
            centers_z = np.hstack((centers_xy, z * np.ones((len(centers_xy), 1))))
            
            counts_z = self.pointcloud.kdt.query_radius(centers_z, r = np.sqrt(3 * max_voxel_size ** 2), count_only = True)
            centers_z = centers_z[counts_z != 0]
            centers.append(centers_z)
        
        centers = np.vstack(centers)
        
        t0 = time.time()
        kdtc = KDTree(centers)
        t1 = time.time()
        print(f"KDTree built on centers in {t1 - t0:.2f}s")
        
        dist, ctr = kdtc.query(pts, k = 1)
        dist, ctr = dist.T[0], ctr.T[0]
        ctrunique = np.unique(ctr)
        
        t2 = time.time()
        print(f"Finished computing voxel association in {t2-t1:.2f}s")
        
        modulo = len(ctrunique) // 100
        print("Saving voxels")
        for i in tqdm(range(len(ctrunique))):
            # if i % modulo == 0:
            #     print(f"{100*i / len(ctrunique):.1f}%")
            voxel_idxs = np.where(ctr == ctrunique[i])[0]
            (self.voxels if len(voxel_idxs) >= min_voxel_length else self.too_small_voxels).append(voxel_idxs)
        
    
    def compute_voxels(self, max_voxel_size, min_voxel_length, seed = 42):
        """
            Transforms the point cloud into a list of list of indices
            Each sublist of indices is a voxel, represented by the indices of its points
            /!\ max_voxel_size is a diameter
        """
    
        np.random.seed(seed) # For reproducibility
        
        all_idxs = np.array(range(len(self.pointcloud)))
        available_idxs = all_idxs.copy()
        available_idxs_mask = np.array(len(self.pointcloud) * [True])
        
        while len(available_idxs) > 0:
            print(len(available_idxs))
            
            # Picking one available index at random
            picked_idx = available_idxs[np.random.randint(0, len(available_idxs))]
            neighbours_idxs = self.pointcloud.get_r_nn([picked_idx], r = max_voxel_size / 2)[0]
            
            # Filtering results to keep the points which have not already been selected in voxels and determining bounding box
            neighbours_idxs = neighbours_idxs[available_idxs_mask[neighbours_idxs]]
            neighbours_coordinates = self.pointcloud.get_coordinates(neighbours_idxs)
            min_voxel, max_voxel = np.min(neighbours_coordinates, axis = 0), np.max(neighbours_coordinates, axis = 0)
            
            # Extracting all points withing the bounding box and filtering  to keep admissible points
            voxel_idxs_mask = np.all(self.pointcloud.get_coordinates() >= min_voxel, axis = 1) & np.all(self.pointcloud.get_coordinates() <= max_voxel, axis = 1)
            voxel_idxs_mask = voxel_idxs_mask & available_idxs_mask
            voxel_idxs = np.where(voxel_idxs_mask)[0]
            
            # These indices are not available anymore
            available_idxs_mask = available_idxs_mask & ~voxel_idxs_mask
            available_idxs = all_idxs[available_idxs_mask]
            
            # Storing into voxels
            (self.voxels if len(voxel_idxs) >= min_voxel_length else self.too_small_voxels).append(voxel_idxs)
        
    def postprocess_small_voxels(self, threshold_grow):
        """
            Transforms the list of too_small_voxels by:
                - either putting them in bigger voxels if they are not too far from one of them (using threshold_grow)
                - either storing them in unassociated_too_small_voxels
        """
        
        # Postprocessing voxels : computes size and geometric centers of voxels at this step before small voxels management 
        gc1 = np.ones((len(self.voxels), 3))
        gc2 = np.ones((len(self.too_small_voxels), 3))
        s1 = np.ones((len(self.voxels), 3))
        for i in range(len(self)):
            coordinates_voxel_i = self.pointcloud.get_coordinates(self.voxels[i])
            vmin, vmax = np.min(coordinates_voxel_i, axis=0), np.max(coordinates_voxel_i, axis=0)
            gc1[i] = (vmin + vmax) / 2
            s1[i] = vmax - vmin
        for i in range(len(self.too_small_voxels)):
            coordinates_voxel_i = self.pointcloud.get_coordinates(self.too_small_voxels[i])
            vmin, vmax = np.min(coordinates_voxel_i, axis=0), np.max(coordinates_voxel_i, axis=0)
            gc2[i] = (vmin + vmax) / 2
            
        # Now we associate small voxels to bigger ones if we can if they are not too far when compared to the size if the big voxel
        kdtv1 = KDTree(gc1)
        dist, nnidx = kdtv1.query(gc2)
        dist, nnidx = dist.T[0], nnidx.T[0]
        admitted = np.all(abs(gc2 - gc1[nnidx]) < threshold_grow * s1[nnidx], axis=1) # Mask of the small voxels that we accept to associate to bigger voxels
        self.unassociated_too_small_voxels = [] # Contains the indices of the small voxels which we will not associate to bigger ones
        for i in range(len(self.too_small_voxels)):
            if admitted[i]:
                self.voxels[nnidx[i]] = np.hstack((self.voxels[nnidx[i]], self.too_small_voxels[i]))
            else:
                self.unassociated_too_small_voxels.append(self.too_small_voxels[i])
                
        self.too_small_voxels = self.unassociated_too_small_voxels
        
    def compute_features(self):
        """
            Computes voxel features and stores them in the variables declared in the constructor
        """
        for i in range(len(self)):
            coordinates_voxel_i = self.pointcloud.get_coordinates(self.voxels[i])
            vmin, vmax = np.min(coordinates_voxel_i, axis=0), np.max(coordinates_voxel_i, axis=0)
            eigval, eigvec = local_PCA(coordinates_voxel_i)
            
            self.features['nb_points'][i] = len(self.voxels[i])
            self.features['geometric_center'][i] = (vmin + vmax) / 2
            self.features['size'][i] = vmax - vmin
            self.features['normal'][i] = eigvec[:, 2]
            self.features['barycenter'][i] = np.sum(coordinates_voxel_i, axis=0) / len(self.voxels[i])
            if self.has_laser_intensity():
                self.features['mean_intensity'][i] = self.pointcloud.get_laser_intensity(self.voxels[i]).mean()
                self.features['var_intensity'][i] = self.pointcloud.get_laser_intensity(self.voxels[i]).var()
            if self.has_color():
                self.features['mean_color'][i] = self.pointcloud.get_color(self.voxels[i]).mean(axis = 0)
                self.features['var_color'][i] = self.pointcloud.get_color(self.voxels[i]).var(axis = 0)
            self.features['verticality'][i], self.features['linearity'][i], self.features['planarity'][i], self.features['sphericity'][i] = features_from_PCA(eigval, eigvec)
            if self.has_label():
                counts = np.bincount(self.get_labels_of_3D_points(i))
                self.features['majority_label'][i] = np.argmax(counts)
                self.features['certainty_label'][i] = np.max(counts) / np.sum(counts)
            
            
    def remove_some_voxels(self, idxs_to_remove):
        """
            In case we need to remove some of the voxels after postprocessing:
            we need to remove them from the list of voxels and recompute the features
            
            idxs_to_remove should be a numpy array of indices
        """
        
        remove_mask = np.zeros(len(self), dtype=bool)
        remove_mask[idxs_to_remove] = True
        
        arr_vx = np.array(self.voxels)
        
        for k in self.features.keys():
            self.features[k] = self.features[k][~remove_mask]
            
        self.removed_voxels.extend(list(arr_vx[remove_mask]))
        self.voxels = list(arr_vx[~remove_mask])
        
        self.kdt = KDTree(self.features['geometric_center'])
        
    
    def are_neighbours(self, i, j, c_D):
        """
            Generates a mask that tells whether one s-voxels and a group of s-voxels are neighbours or not (using the conditions from the link-chain method, cf. article)
            i : index
            j : index or list of indices
        """
        # TODO Can this be considered as a particular case of the similarity function ?
        
        isnum = False
        if type(j) is int:
            isnum = True
            j = [j]
        
        gc_target = self.features['geometric_center'][i, :]
        gc_candidates = self.features['geometric_center'][j, :]
        size_target = self.features['size'][i, :]
        size_candidates = self.features['size'][j, :]
        vi_target = self.features['var_intensity'][i]
        vi_candidates = self.features['var_intensity'][j]
        mi_target = self.features['mean_intensity'][i]
        mi_candidates = self.features['mean_intensity'][j]
        vc_target = self.features['var_color'][i, :]
        vc_candidates = self.features['var_color'][j, :]
        mc_target = self.features['mean_color'][i, :]
        mc_candidates = self.features['mean_color'][j, :]
        
        w_D = (size_target + size_candidates) / 2
        cond_D = np.all(abs(gc_target - gc_candidates) <= w_D + c_D, axis=1)
        
        cond_I = np.ones(len(j), dtype=bool)
        if self.has_laser_intensity() is not None:
            w_I = np.maximum(vi_target, vi_candidates)
            cond_I = abs(mi_target - mi_candidates) <= 3 * np.sqrt(w_I)
            
        cond_C = np.ones(len(j), dtype=bool)
        if self.has_color() is not None:
            w_C = np.maximum(vc_target, vc_candidates)
            cond_C = np.all(abs(mc_target - mc_candidates) <= 3 * np.sqrt(w_C), axis=1)
        
        cond = cond_D & cond_I & cond_C
        if isnum:
            return cond[0]
        
        return cond
        
    def are_neighbours_clf(self, i, j, c_D):
        """
            Generates a mask that tells whether one s-voxels and a group of s-voxels are neighbours or not (using the specified classifier)
            i : index
            j : index or list of indices
        """
        
        assert self.neighbourhood_classifier is not None, 'no neighborhood classifier has been defined'
        
        isnum = False
        if type(j) is int:
            isnum = True
            j = [j]
            
        neighbours_features, _ = self.compute_neighbourhood_feature_and_label(i, j)
        predicted_neighbours_labels = self.neighbourhood_classifier.predict(neighbours_features).astype(bool)
            
        if isnum:
            return predicted_neighbours_labels[0]
        
        return predicted_neighbours_labels
    
    def similarity(self, i, j, c_D, weights = [1/3, 1/3, 1/3]):
        """
            Numeric representation of the similarity between two voxels
            i : index
            j : index or list of indices
        
            We define the similarity as follows:
                0 if the voxels are spatially too far from each other (not found in find_spatial_neighbours)
                otherwise: 
                
        """
        
        isnum = False
        if type(j) is int:
            isnum = True
            j = [j]
            
        gc_target = self.features['geometric_center'][i, :]
        gc_candidates = self.features['geometric_center'][j, :]
        size_target = self.features['size'][i, :]
        size_candidates = self.features['size'][j, :]
        vi_target = self.features['var_intensity'][i]
        vi_candidates = self.features['var_intensity'][j]
        mi_target = self.features['mean_intensity'][i]
        mi_candidates = self.features['mean_intensity'][j]
        vc_target = self.features['var_color'][i, :]
        vc_candidates = self.features['var_color'][j, :]
        mc_target = self.features['mean_color'][i, :]
        mc_candidates = self.features['mean_color'][j, :]
        
        sim = np.zeros(len(j))
        
        w_D = (size_target + size_candidates) / 2
        w_I = np.maximum(vi_target, vi_candidates)
        w_C = np.maximum(vc_target, vc_candidates)
        
        sim += weights[0] * np.sum(np.maximum(1 - abs(gc_target - gc_candidates) / (w_D + c_D), np.zeros((len(j), 3))), axis = 1)
        
        if self.has_laser_intensity() is not None:
            sim += weights[1] * np.maximum(1 - abs(mi_target - mi_candidates) / 3 / (np.sqrt(w_I) + 1e-6), np.zeros(len(j)))
        
        if self.has_color() is not None:
            sim += weights[2] * np.sum(np.maximum(1 - abs(mc_target - mc_candidates) / 3 / (np.sqrt(w_C) + 1e-6), np.zeros((len(j), 3))), axis = 1)
        
        return sim[0] if isnum else sim
    
    def build_similarity_graph(self, c_D, weights, sparse_matrix = True):
        if sparse_matrix:
            A = sparse.lil_matrix((len(self), len(self)))
            D = sparse.lil_matrix((len(self), len(self)))
        else:
            A = np.zeros((len(self), len(self)))
            D = np.zeros((len(self), len(self)))
        
        neighbours = []
        idxs = list(range(len(self)))
        potential = self.find_spatial_neighbours(idxs, c_D)
        for i in range(len(self)):
            similarity = self.similarity(i, potential[i], c_D, weights)
            A[i, potential[i]] = similarity
            D[i,i] = np.sum(similarity)
            
        return A, D
        
    def find_connected_components_similarity(self, c_D, weights, K):
        print("Building similarity graph")
        A, D = self.build_similarity_graph(c_D, weights)
        L = D - A
        eps = 1e-4
        L += eps * sparse.eye(L.shape[0])
        print("Finding the eigendecomposition of the Laplacian graph... This may require time.")
		
        eigenvalues, eigenvectors = sparse.linalg.eigsh(L, k=K, sigma=0)
        # eigenvalues, eigenvectors = np.linalg.eigh(L)
		
        print("Successfully diagonalized Laplacian matrix")
        kmeans = KMeans(n_clusters=K)
        #pca = PCA(n_components = 100)
        #eigenvectors = pca.fit_transform(eigenvectors)
        eigenvectors = normalize(eigenvectors)
        lbl = kmeans.fit_predict(eigenvectors[:,:K])
        # gm = GaussianMixture(K)
        # lbl = gm.fit_predict(eigenvectors[:,:K])
        
        components_idx = np.unique(lbl)
        components = []
        
        for i in components_idx:
            components.append(np.where(lbl == i)[0])
        
        return components
        
    def find_connected_components_similarity_v2(self, c_D, weights, K):
        
        print("Building similarity matrix")
        A = sparse.lil_matrix((len(self), len(self)))
        neighbours = []
        idxs = list(range(len(self)))
        potential = self.find_spatial_neighbours(idxs, c_D)
        for i in range(len(self)):
            similarity = self.similarity(i, potential[i], c_D, weights)
            A[i, potential[i]] = similarity
        A = A.tocsr()
        
        print("Spectral clustering")
        clustering = SpectralClustering(n_clusters = K, affinity = "precomputed", assign_labels = "kmeans", random_state=0, n_jobs = -1)
        lbl = clustering.fit_predict(A)
        
        print("Attributing components")
        components_idx = np.unique(lbl)
        components = []
        
        for i in components_idx:
            components.append(np.where(lbl == i)[0])
        
        return components
        
    def compute_neighbourhood_feature_and_label(self, i, j):
        """
            For each potential pair of voxel neighbours compute features describing the pair and label (whether it's a true pair or not)
            i : index
            j : index or list of indices
            
        """
        
        CERTAINTY_THRESHOLD = 0.8
        
        isnum = False
        if type(j) is int:
            isnum = True
            j = [j]
        
        max_z = np.maximum(self.features['geometric_center'][:,2][j], self.features['geometric_center'][:,2][i])
        delta_geometric_center = abs(self.features['geometric_center'][j] - self.features['geometric_center'][i])
        mean_normal = (self.features['normal'][j] + self.features['normal'][i]) / 2
        delta_normal = abs(self.features['normal'][j] - self.features['normal'][i])
        delta_barycenter = abs(self.features['barycenter'][j] - self.features['barycenter'][i])
        mean_intensity = (self.features['mean_intensity'][j] + self.features['mean_intensity'][i]) / 2
        delta_mean_intensity = abs(self.features['mean_intensity'][j] - self.features['mean_intensity'][i])
        max_var_intensity = np.maximum(self.features['var_intensity'][j], self.features['var_intensity'][i])
        mean_color = (self.features['mean_color'][j] + self.features['mean_color'][i]) / 2
        delta_mean_color = abs(self.features['mean_color'][j] - self.features['mean_color'][i])
        max_var_color = np.maximum(self.features['var_color'][j], self.features['var_color'][i])
        max_verticality = np.maximum(self.features['verticality'][j], self.features['verticality'][i])
        delta_verticality = abs(self.features['verticality'][j] - self.features['verticality'][i])
        max_linearity = np.maximum(self.features['linearity'][j], self.features['linearity'][i])
        delta_linearity = abs(self.features['linearity'][j] - self.features['linearity'][i])
        max_planarity = np.maximum(self.features['planarity'][j], self.features['planarity'][i])
        delta_planarity = abs(self.features['planarity'][j] - self.features['planarity'][i])
        max_sphericity = np.maximum(self.features['sphericity'][j], self.features['sphericity'][i])
        delta_sphericity = abs(self.features['sphericity'][j] - self.features['sphericity'][i])
        
        features = np.hstack((max_z[:, None],
                              delta_geometric_center,
                              mean_normal,
                              delta_normal,
                              delta_barycenter,
                              (mean_intensity[:, None] if self.has_laser_intensity() else np.empty((len(j),0))),
                              (delta_mean_intensity[:, None] if self.has_laser_intensity() else np.empty((len(j),0))),
                              (max_var_intensity[:, None] if self.has_laser_intensity() else np.empty((len(j),0))),
                              (mean_color if self.has_color() else np.empty((len(j),0))),
                              (delta_mean_color if self.has_color() else np.empty((len(j),0))),
                              (max_var_color if self.has_color() else np.empty((len(j),0))),
                              max_verticality[:, None],
                              delta_verticality[:, None],
                              max_linearity[:, None],
                              delta_linearity[:, None],
                              max_planarity[:, None],
                              delta_planarity[:, None],
                              max_sphericity[:, None],
                              delta_sphericity[:, None]))

        if self.has_label():
            labels = ((self.features['majority_label'][j] == self.features['majority_label'][i]) & (np.minimum(self.features['certainty_label'][j], self.features['certainty_label'][i]) > CERTAINTY_THRESHOLD)).astype(int)
        else:
            labels = np.nan * np.ones(len(j))
        
        return features, labels
        
    def get_cond_D_mask(self, i, j, c_D):
        """
            Generates a mask that tells whether one s-voxels and a group of s-voxels satisfy condition on distances
            i : index
            j : index or list of indices
        """

        isnum = False
        if type(j) is int:
            isnum = True
            j = [j]
        
        gc_target = self.features['geometric_center'][i, :]
        gc_candidates = self.features['geometric_center'][j, :]
        size_target = self.features['size'][i, :]
        size_candidates = self.features['size'][j, :]
        
        w_D = (size_target + size_candidates) / 2
        cond_D = np.all(abs(gc_target - gc_candidates) <= w_D + c_D, axis=1)
        
        if isnum:
            return cond_D[0]
        
        return cond_D
        
    def get_train_neighbourhood_feature_and_label(self, c_D, segment_out_ground, threshold_in, threshold_normals):
        """
            Returns feature and label for neighbourhood classifier
        """
        
        assert self.has_label(), 'to train a neighborhood classifier you need to provide ground thruth labels'
        
        neighbours_features = []
        neighbours_labels = []
        indices = np.array(range(len(self)))
        
        # Segment out the ground
        if segment_out_ground:
            NB_RANDOM_DRAWS = 1000
            best_ref_pt, best_normal = RANSAC(self.features['geometric_center'], self.features['normal'], NB_RANDOM_DRAWS, threshold_in, threshold_normals)
            ground_mask = in_plane(self.features['geometric_center'], self.features['normal'], best_ref_pt, best_normal, threshold_in, threshold_normals)
            indices = indices[~ground_mask]
        
        # Compute features and labels
        potential = self.find_spatial_neighbours(indices, c_D)
        for idx in range(len(indices)):
            unique_mask = (potential[idx] > indices[idx])
            cond_D_mask = self.get_cond_D_mask(indices[idx], potential[idx], c_D)
            mask = (unique_mask & cond_D_mask)
            idx_features, idx_labels = self.compute_neighbourhood_feature_and_label(indices[idx], potential[idx][mask])
            if len(idx_features) > 0:
                neighbours_features.append(idx_features)
                neighbours_labels.append(idx_labels)
        
        # Balance positive and negative samples
        neighbours_features = np.vstack(neighbours_features)
        neighbours_labels = np.hstack(neighbours_labels)
        pos_mask = (neighbours_labels == 1)
        pos_idxes = np.array(range(len(neighbours_features)))[pos_mask]
        neg_idxes = np.array(range(len(neighbours_features)))[~pos_mask]
        if len(pos_idxes) > len(neg_idxes):
            pos_idxes = list(pos_idxes)
            random.shuffle(pos_idxes)
            neg_idxes = list(neg_idxes)
            pos_idxes = pos_idxes[:len(neg_idxes)]
            all_idxes = pos_idxes + neg_idxes
            random.shuffle(all_idxes)
            all_idxes = np.array(all_idxes)
            neighbours_features = neighbours_features[all_idxes]
            neighbours_labels = neighbours_labels[all_idxes]
        
        return neighbours_features, neighbours_labels

    def set_neighbourhood_classifier(self, neighbourhood_classifier):
        self.neighbourhood_classifier = neighbourhood_classifier

    def find_spatial_neighbours(self, idxs, c_D, exclude_self = True):
        """
            Returns a list of indices of potential neighbouring voxels
        """
        
        max_size = np.max(self.features['size'])
        if type(idxs) is int:
            res = self.kdt.query_radius(self.features['geometric_center'][[idxs], :], r = max_size + c_D)[0]
            return res[res != idxs] if exclude_self else res
        
        res = self.kdt.query_radius(self.features['geometric_center'][idxs, :], r = max_size + c_D)
        if exclude_self:
            for i in range(len(idxs)):
                res[i] = res[i][res[i] != idxs[i]]
        return res
    
    def find_neighbours(self, idxs, c_D):
        """
            Returns a list of all indices who are truly neighbours of each index in idxs
        """
        
        isnum = False
        if type(idxs) is int:
            isnum = True
            idxs = [idxs]
        
        neighbours = []
        potential = self.find_spatial_neighbours(idxs, c_D)
        
        # If a neighbourhood classifier has been specified use it to find neighbours
        if self.neighbourhood_classifier is not None:
            # Filter potential neighbours based on condition on distances
            for i in range(len(idxs)):
                cond_D_mask = self.get_cond_D_mask(idxs[i], potential[i], c_D)
                potential[i] = potential[i][cond_D_mask]
            are_neighbours = self.are_neighbours_clf
            
        else:
            are_neighbours = self.are_neighbours

        for i in range(len(idxs)):
            neighbours.append(potential[i][are_neighbours(idxs[i], potential[i], c_D)])
            
        if isnum:
            return neighbours[0]
            
        return neighbours
    
    def compute_connected_components(self, c_D, segment_out_ground, threshold_in, threshold_normals, min_component_length):
        """
            Builds a list of connected components of voxels from a voxelcloud object,
            by performing a depth first search by using the neighbourhood condition
            of voxels
            
            The list of connected components is then returned
            
            Each item of self.component is a list of indices, which are the indices of
            the underlying VoxelCloud object
            eg. components[i] = [1, 2, 3]
                means that this component #i is made up of voxels 1, 2 and 3
        """
        
        n_voxels = len(self)
        voxel_neighbours = self.find_neighbours(list(range(n_voxels)), c_D)
        
        # Initialize connected components
        components = []
        too_small_components = []
        indices = np.array(list(range(n_voxels)))
        indices_mask = np.ones(n_voxels, dtype=bool)
        
        # Segment out the ground
        if segment_out_ground:
            NB_RANDOM_DRAWS = 1000
            best_ref_pt, best_normal = RANSAC(self.features['geometric_center'], self.features['normal'], NB_RANDOM_DRAWS, threshold_in, threshold_normals)
            ground_mask = in_plane(self.features['geometric_center'], self.features['normal'], best_ref_pt, best_normal, threshold_in, threshold_normals)
            ground_idxes = indices[ground_mask]
            indices_mask[ground_idxes] = False
            indices = np.array(list(range(n_voxels)))[indices_mask]
            components.append(list(ground_idxes))
        
        # Explore connected components
        while len(indices) > 0:
            stack = [indices[0]]
            current_component = []
            
            # Run a depth first search to find all connected voxels
            while len(stack) > 0:
                idx = stack.pop()
                if ~indices_mask[idx]:
                    continue
                current_component.append(idx)
                indices_mask[idx] = False 
                next_idxs = voxel_neighbours[idx]
                stack.extend(list(next_idxs[indices_mask[next_idxs]]))
            if len(current_component) >= min_component_length:
                components.append(current_component)
            else:
                too_small_components.append(current_component)
            
            # Updating indices
            indices = np.array(list(range(n_voxels)))[indices_mask]
        
        return components, too_small_components
    
    def remove_poorly_connected_voxels(self, c_D, threshold_nb_voxels):
        """
            Computes the connected components for the specified parameters of neighbourhood function,
            then removed the connected components which have less voxels than the threshold
        """
        
        cp = self.compute_connected_components(c_D, segment_out_ground = False, threshold_in = None, threshold_normals = None, min_component_length = 1)[0]
        lengths = np.zeros(len(cp))
        for i in range(len(cp)):
            lengths[i] = len(cp[i])
        
        idxs_cp_under_threshold = lengths < threshold_nb_voxels
        nb_components_to_remove = np.sum(idxs_cp_under_threshold)
        
        if nb_components_to_remove > 0:
            voxels_to_remove = np.hstack(np.array(cp)[idxs_cp_under_threshold])
            self.remove_some_voxels(voxels_to_remove)
        else:
            voxels_to_remove = []
        
        return len(voxels_to_remove), nb_components_to_remove
        
    
    def get_labels_of_3D_points(self, i):
        """
            Fetches all the labels of the underlying 3D points for a voxel id or a list of voxel ids
            According to the type of i (integer or iterable of integers), the result
            shall be a 1-d numpy array or a list of 1-d numpy arrays (of different sizes)
            
            i may be an integer or a list of integers
        """
        if type(i) is int:
            return self.pointcloud.get_label(self.voxels[i])
        
        labels = []
        for ii in i:
            labels.append(self.pointcloud.get_label(self.voxels[ii]))
        return labels
    
    def get_all_3D_points(self, i):
        """
            Fetches all the underlying 3D points for a voxel id or a list of voxel ids
            According to the type if i (integer or iterable of integers), the result
            shall be a Nx3 numpy array or a list of Nx3 numpy arrays
            
            i may be integer or a list of integers
        """
        if type(i) is int:
            return self.pointcloud.get_coordinates(self.voxels[i])
        
        points = []
        for ii in i:
            points.append(self.pointcloud.get_coordinates(self.voxels[ii]))
        return points
    
    def get_all_unassociated_3D_points(self):
        """
            Fetches all the 3D points of voxels which were too small and that we weren't
            able to associate to any bigger voxel
        """
        results = []
        for i in self.too_small_voxels:
            results.append(self.pointcloud.get_coordinates(i))
        return np.vstack(results) if len(results) > 0 else np.array([])
    
    def get_all_removed_3D_points(self):
        """
            Fetches all the 3D points of voxels which were removed using the remove_some_voxels function
        """
        results = []
        for i in self.removed_voxels:
            results.append(self.pointcloud.get_coordinates(i))
        return np.vstack(results) if len(results) > 0 else np.array([])
    
    def has_laser_intensity(self):
        """
            Tells whether this voxel cloud has laser intensity information
        """
        return self.pointcloud.has_laser_intensity()
    
    def has_color(self):
        """
            Tells whether this voxel cloud has RGB color information
        """
        return self.pointcloud.has_color()
    
    def has_label(self):
        """
            Tells whether this voxel cloud has label information
        """
        return self.pointcloud.has_label()
    
    def __len__(self):
        return len(self.voxels)