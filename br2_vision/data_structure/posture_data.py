import operator
import os
from dataclasses import dataclass
from typing import List, Tuple

import h5py
import numpy as np
from nptyping import Floating, NDArray, Shape
from sklearn.linear_model import LinearRegression
from tqdm import tqdm

from br2_vision.utility.convert_coordinate import get_center_and_normal
from br2_vision.utility.logging import get_script_logger

from .tracking_data import TrackingData
from .utils import raise_if_outside_context


def compute_positions_and_directors(
    path: str,
) -> tuple[
    NDArray[Shape["T, [x,y,z], N"], Floating], NDArray[Shape["T, 3, 3, N"], Floating]
]:
    """
    Compute positions and directors from the trajectory data
    """
    cross_section_center_position = []
    cross_section_director = []

    with TrackingData.load(path) as dataset:
        marker_positions = dataset.marker_positions

        for z_index in tqdm(range(len(marker_positions)), position=1):
            euler_coords = []
            data_collection = []
            for tag in marker_positions.tags:
                data = dataset.load_track(z_index, tag)
                if data is None:
                    continue
                euler_coords.append(marker_positions.get_position(z_index, tag))
                data_collection.append(data)
            euler_coords = np.array(euler_coords)

            positions = []
            directors = []
            for tidx in tqdm(range(len(data_collection[0])), position=0):
                P = np.array([data[tidx] for data in data_collection])  # shape: [N, 3]
                nan_index = np.isnan(P).any(axis=1)
                P = P[~nan_index]
                R = euler_coords[~nan_index]
                # AR = P
                A = LinearRegression().fit(R, P)

                positions.append(A.predict([marker_positions.origin])[0])
                directors.append(A.predict(marker_positions.Q.T).T)
            cross_section_center_position.append(np.array(positions))
            cross_section_director.append(np.array(directors))

    positions = np.stack(cross_section_center_position, axis=-1)
    directors = np.stack(cross_section_director, axis=-1)

    print("Posture Interpolation")
    print(f"{positions.shape=}")
    print(f"{directors.shape=}")

    return positions, directors


class PostureData:
    """
    Data structure for postures
    """

    def __init__(self, path):
        self.path = path
        self.logger = get_script_logger(os.path.basename(__file__))

        self._inside_context = False

        self._positions: NDArray[Shape["T, [x,y,z], N"], Floating] = None
        self._positions_key: str = "/posture/positions"
        self._directors: NDArray[Shape["T, 3, 3, N"], Floating] = None
        self._directors_key: str = "/posture/directors"
        self._time: NDArray[Shape["T"], Floating] = None
        self._time_key: str = "/dlt-track/timestamps"

    @raise_if_outside_context
    def get_time(self):
        return self._time

    @raise_if_outside_context
    def get_cross_section_center_position(self):
        return self._positions

    @raise_if_outside_context
    def get_cross_section_director(self):
        return self._directors

    @raise_if_outside_context
    def save_positions_and_directors(self):
        with h5py.File(self.path, "a") as h5f:
            position_dataset = h5f.require_dataset(
                self._positions_key,
                self._positions.shape,
                dtype=np.float64,
            )
            position_dataset[...] = self._positions
            position_dataset.attrs["unit"] = "m"

            director_dataset = h5f.require_dataset(
                self._directors_key,
                self._directors.shape,
                dtype=np.float64,
            )
            director_dataset[...] = self._directors
            director_dataset.attrs["unit"] = "m"

        print("Posture saved.")

    @raise_if_outside_context
    def load_positions_and_directors(self):
        """
        Load position and directors if exists.
        If not, compute them from the trajectory data.
        """

        flag = False
        with h5py.File(self.path, "r") as h5f:
            if self._positions_key in h5f:
                self._positions = np.array(h5f[self._positions_key], dtype=np.float64)
            else:
                flag = True

            if self._directors_key in h5f:
                self._directors = np.array(h5f[self._directors_key], dtype=np.float64)
            else:
                flag = True

            self._time = np.array(h5f[self._time_key], dtype=np.float64)

        if flag:
            self.logger.info("Computing positions and directors...")
            self._positions, self._directors = compute_positions_and_directors(
                self.path
            )

    def __enter__(self):
        """
        If file at self.path does not exist, create one.
        """
        self._inside_context = True
        assert os.path.exists(self.path), f"File does not exist {self.path}."
        self.load_positions_and_directors()
        return self

    def __exit__(self, exception_type, exception_value, exception_traceback):
        """
        Save posture on the existing file
        """
        self.save_positions_and_directors()
        self._inside_context = False
