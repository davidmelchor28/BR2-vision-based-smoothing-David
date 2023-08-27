import numpy as np
import os, sys

from utility.convert_coordinate import get_center_and_normal

from config import *

def main(runid, n_ring):
    # Read DLT Point Data
    output_file_path = PREPROCESSED_POSITION_PATH.format(runid)
    data = np.load(file_path)

    position_collection = data['position']
    tags = data['tags']
    timelength = position_collection.shape[1]

    # Find cross-section data for each timeframe
    cross_section_center_position = []
    cross_section_director = []
    for time in range(timelength): 
        labelled_points = {}
        for tag, points in zip(tags, position_collection[:,time,:]):
            if np.all(np.isnan(points)):
                continue
            labelled_points[tag] = points
        center_position, director_vector = get_center_and_normal(labelled_points, n_ring=n_ring)
        cross_section_center_position.append(center_position)
        cross_section_director.append(director_vector)
    cross_section_center_position = np.array(cross_section_center_position)
    cross_section_director = np.array(cross_section_director)

    # Append in the same file
    print(f'{cross_section_center_position.shape=}')
    print(f'{cross_section_director.shape=}')
    print(cross_section_center_position[0,...])
    data = dict(data)
    data['cross_section_center_position'] = cross_section_center_position
    data['cross_section_director'] = cross_section_director
    np.savez(
        output_file_path,
        **data,
    )

    # Verbose
    print('Data saved: {}'.format(output_file_path))

if __name__=="__main__":
    runid = 1
    main(runid, n_ring=5)

