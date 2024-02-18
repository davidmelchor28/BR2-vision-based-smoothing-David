import os, sys

import numpy as np
import matplotlib.pyplot as plt
#from collections import defaultdict

from tqdm import tqdm
from itertools import combinations

import cv2
#from dlt import DLT
#from cv2_custom.transformation import scale_image
#from cv2_custom.marking import cv2_draw_label

import br2_vision
from br2_vision.utility.logging import config_logging, get_script_logger
from br2_vision.data import MarkerPositions, TrackingData, FlowQueue
from br2_vision.cv2_custom.extract_info import get_video_frame_count
from br2_vision.cv2_custom.transformation import flat_color

#from sklearn.linear_model import LinearRegression
#from scipy.spatial.distance import directed_hausdorff
#from scipy.signal import savgol_filter as sgfilter

# Optical Flow and Point Detection Module
class CameraOpticalFlow:
    """
    Optical Flow and Point Detection Module
    """
    # Configuration: Corner detection
    # parameters for ShiTomasi corner detection
    _feature_params = dict(maxCorners=100, qualityLevel=0.3, minDistance=7, blockSize=7)

    # Configuration: Corner SubPix
    _subpix_criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001)

    # Configuration: Optical Flow
    # Parameters for lucas kanade optical flow
    _lk_params = dict(
        winSize=(15, 15),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 35, 0.0001),
        flags=cv2.OPTFLOW_LK_GET_MIN_EIGENVALS,
        minEigThreshold=0.000,
    )  # 0.017

    # Configuration: Color scheme
    _color = np.random.randint(0, 235, (100, 3)).astype(int)  # 100 points for now

    def __init__(self, video_path, flow_queues:FlowQueue, dataset: TrackingData, debug=False):
        self.video_path = video_path

        self.flow_queues = flow_queues
        self.dataset = dataset

    @property
    def num_frames(self):
        """
        Get the number of frames in the video
        """
        if self._num_frames is None:
            self._num_frames = get_video_frame_count(self.video_path)
        return self._num_frames

    def run(self):
        for queue in self.flow_queues:
            data_collection = self.next_inquiry(queue)
            self.dataset.save_flow_trajectory(data_collection, queue, self.num_frames)

    def get_points_in_order(self, keys):
        points = []
        for key in keys:
            points.append(self.p[key])
        return points

    def get_point_info(self, key):
        info = {}
        info["point"] = self.p[key]
        info["is_occluded"] = self.is_occluded[key]
        if self.is_occluded[key]:
            info["predicted_point"] = self.reappearance_module.get_predicted_point(key)
        return info

    def get_current_frame(self, mark_trajectory=False, mark_points=False):
        frame = self.current_frame.copy()
        if mark_trajectory:
            color = plt.get_cmap("hsv")(np.linspace(0, 1, 20)) * 255
            trajectories = self.reappearance_module.trajectory_block[mark_trajectory]
            for idx, trajectory in enumerate(trajectories):
                if len(trajectory) <= 1:
                    continue
                self.draw_track(frame, trajectory[:-1], trajectory[1:], color[idx])
                if mark_points:
                    for point in trajectory:
                        frame = cv2.circle(
                            frame, (int(point[0]), int(point[1])), 13, color[idx], -1
                        )
        return frame

    def get_none_occluded_points(self):
        points = []
        keys = []
        for k, is_occluded in self.is_occluded.items():
            if is_occluded:
                continue
            points.append(self.p[k])
            keys.append(k)
        return np.array(points), keys

    def get_none_occluded_points_dict(self):
        points = {}
        for k, is_occluded in self.is_occluded.items():
            if is_occluded:
                continue
            points[k] = self.p[k]
        return points

    def draw_points(self, frame, points, radius=8, color=(0, 235, 0), thickness=-1):
        # draw the points (overlay)
        for i, point in enumerate(points):
            a, b = point.ravel()
            a, b = int(a), int(b)
            frame[:] = cv2.circle(frame, (a, b), radius, color, thickness)

    def draw_track(self, mask, p0, p1, color=(0, 235, 0)):
        # draw the tracks (overlay)
        for i, (new, old) in enumerate(zip(p1, p0)):
            a, b = new.ravel()
            a, b = int(a), int(b)
            c, d = old.ravel()
            c, d = int(c), int(d)
            # mask = cv2.line(mask, (a,b),(c,d), color[i].tolist(), 2)
            mask[:] = cv2.line(mask, (a, b), (c, d), color, 2)

    def show_frame(self, frame, mask):
        img = cv2.add(frame, mask)
        for key in USED_TAGS:
            a, b = self.p[key]
            a, b = int(a), int(b)
            if self.is_occluded[key]:  # Ocluded points are red x
                cv2.drawMarker(
                    img,
                    (a, b),
                    color=(0, 0, 235),
                    markerType=cv2.MARKER_CROSS,
                    thickness=2,
                )
            else:  # visible points are green o
                img = cv2.circle(img, (a, b), 8, (0, 235, 0), -1)
        cv2.imshow("frame", img)
        cv2.waitKey(0)
        # cv2.destroyAllWindows()

    def render_tracking_video(self, save_path):
        """save_tracking_video

        Parameters
        ----------
        save_path :
            path
        """
        print("Saving tracing video ...")

        cap = cv2.VideoCapture(self.video_path)
        frame_width = int(cap.get(4))
        frame_height = int(cap.get(3))
        writer = cv2.VideoWriter(
            save_path, cv2.VideoWriter_fourcc(*"mp4v"), 60, (frame_height, frame_width)
        )

        # Create a mask image for drawing purposes
        ret, old_frame = cap.read()
        mask = np.zeros_like(old_frame)

        video_length = self.num_frames
        for num_frame in tqdm(range(video_length), miniters=10):
            # while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            # draw the tracks
            good_new = self.points[num_frame + 1, :, :]
            good_old = self.points[num_frame, :, :]
            for i, (new, old) in enumerate(zip(good_new, good_old)):
                tag = self.tags[i]
                a, b = new.ravel()
                a = int(a)
                b = int(b)
                c, d = old.ravel()
                c = int(c)
                d = int(d)
                if (a <= 5 and b <= 5) or (c <= 5 and d <= 5):
                    continue
                mask = cv2.line(
                    mask, (a, b), (c, d), CameraOpticalFlow._color[i].tolist(), 2
                )
                frame = cv2.circle(
                    frame, (a, b), 11, CameraOpticalFlow._color[i].tolist(), -1
                )
                cv2.putText(
                    frame,
                    tag,
                    (a, b + 25),
                    fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                    fontScale=0.5,
                    color=(255, 255, 255),
                    lineType=2,
                )

            img = cv2.add(frame, mask)
            writer.write(img)
        cap.release()
        cv2.destroyAllWindows()
        writer.release()

    def crop_roi(self, image, uv, scale=1.0, disp_h=80, disp_w=80):
        x, y = uv.ravel()
        x = int(x)
        y = int(y)

        # Region of interest display
        padded_img = cv2.copyMakeBorder(
            image,
            disp_h // 2,
            disp_h // 2,
            disp_w // 2,
            disp_w // 2,
            cv2.BORDER_CONSTANT,
            value=[0, 0, 0],
        )
        cropped_img = padded_img[y : y + disp_h, x : x + disp_w]
        return cropped_img

    def next_inquiry(self, debug=False):
        if len(self.inquiries) == 0:  # Check if any inquiries left
            print("No inquiry left")
            return

        tags, stime, etime = self.inquiries.pop(0)
        self.history.append((tags, stime, etime))
        if stime >= self.num_frames
            print("start frame greater than total video frame")
            return

        # Forward Flow
        # Load video
        cap = cv2.VideoCapture(self.video_path)
        assert cap.isOpened(), "Video is not properly opened: {}".format(
            self.video_path
        )

        # Read frames
        cap.set(cv2.CAP_PROP_POS_FRAMES, stime)
        ret, frame = cap.read()
        old_gray = flat_color(frame)

        # Tag
        tag_order = []
        for tag in tags:
            tag_order.append(self.tags.index(tag))
        tag_order = np.array(tag_order, dtype=int)
        self.points[stime + 1 : etime, tag_order, :] = 0.0
        p0 = self.points[stime, tag_order, :].reshape([-1, 1, 2])

        errors = []
        status = np.ones(p0.shape[:2], dtype=bool)
        for num_frame in tqdm(range(etime - stime)):
            ret, frame = cap.read()
            if not ret:
                break
            frame_gray = flat_color(frame)
            # Preprocess (sharpen)
            # sharpen_kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
            # frame_gray = cv2.filter2D(frame_gray, -1, sharpen_kernel)

            # calculate optical flow
            p1, new_status, err = cv2.calcOpticalFlowPyrLK(
                old_gray, frame_gray, p0, None, **CameraOpticalFlow._lk_params
            )
            status = np.logical_and(status, new_status)
            if np.all(~status):
                # If all points are lost, stop the flow
                break
            err[~status[:, 0]] = np.nan
            errors.append(err)

            tracking_tag_order = tag_order[status[:, 0]]
            self.points[stime + num_frame + 1, tracking_tag_order, :] = p1[
                status[:, 0], 0, :
            ]

            # Now update the previous frame and previous points
            old_gray = frame_gray.copy()
            p0 = p1.reshape(-1, 1, 2)

        cap.release()
        cv2.destroyAllWindows()
