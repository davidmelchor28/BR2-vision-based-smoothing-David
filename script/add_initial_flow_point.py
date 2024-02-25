import os, sys
import numpy as np
import pathlib
import cv2
import matplotlib.pyplot as plt

from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QLineEdit, QInputDialog

import click

import br2_vision
from br2_vision.utility.logging import config_logging, get_script_logger
from br2_vision.data import MarkerPositions, TrackingData, FlowQueue
from br2_vision.cv2_custom.marking import cv2_draw_label
from br2_vision.cv2_custom.transformation import scale_image
from br2_vision.qt_custom.label_prompt import LabelPrompt


def on_mouse_zoom(event, x, y, flags, param):
    uv = param["uv"]
    original_uv = param["original_uv"]
    if event == cv2.EVENT_LBUTTONDOWN:
        uv[0] = x
        uv[1] = y
    elif event == cv2.EVENT_RBUTTONDOWN:
        # Return original uv
        uv[:] = original_uv


def zoomed_inquiry(current_frame, uv, scale=5.0, disp_h=80, disp_w=80):
    """
    Inquiry for a point in a zoomed-in region of the image.
    """
    x, y = uv
    x = int(x)
    y = int(y)

    # Region of interest display
    window_name_roi = "zoom-prompt"
    cv2.namedWindow(window_name_roi)
    disp_img_roi = current_frame.copy()
    disp_img_roi = cv2.rectangle(
        disp_img_roi,
        (x - disp_w // 2, y - disp_h // 2),
        (x + disp_w // 2, y + disp_h // 2),
        (0, 0, 255),
        thickness=3,
    )
    cv2.imshow(window_name_roi, disp_img_roi)

    # Transformation
    img = current_frame.copy()
    padded_img = cv2.copyMakeBorder(
        img,
        disp_h // 2,
        disp_h // 2,
        disp_w // 2,
        disp_w // 2,
        cv2.BORDER_CONSTANT,
        value=[0, 0, 0],
    )
    scaled_img = scale_image(padded_img[y : y + disp_h, x : x + disp_w], scale)
    _x = int(disp_w * scale / 2)
    _y = int(disp_h * scale / 2)
    _uv = np.array([_x, _y])

    # Implement mouse event for clicking other point
    original_uv = _uv.copy()

    # Inquiry Loop
    inquiry_on = True
    window_name = "select reappeared point"
    cv2.namedWindow(window_name)
    cv2.setMouseCallback(
        window_name, on_mouse_zoom, param={"uv": _uv, "original_uv": original_uv}
    )
    print("zoomed-in inquiry")
    print("d: cancel, a: accept, h: help")
    while inquiry_on:
        disp_img = scaled_img.copy()

        # Draw cross with exact center _uv
        disp_img[_uv[1] : _uv[1] + 1, :] = np.array([0, 0, 235])
        disp_img[:, _uv[0] : _uv[0] + 1] = np.array([0, 0, 235])

        cv2.imshow(window_name, disp_img)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("d"):  # Cancel: No point found
            inquiry_on = False
            uv = original_uv
        elif key == ord("a"):  # Accept: accept change
            inquiry_on = False
        elif key == ord("h"):  # Help
            print("d: cancel, a: accept, h: help")
        else:
            pass

    cv2.destroyWindow(window_name)
    cv2.destroyWindow(window_name_roi)

    x = int(_uv[0] / scale) + x - disp_w // 2
    y = int(_uv[1] / scale) + y - disp_h // 2

    return np.array([x, y], dtype=int)


# Mouse Handle
prev_tag = ""


def mouse_event_click_point(event, x, y, flags, param):
    global prev_tag
    points = param["points"]
    marker_label = param["marker_label"]
    bypass_inquiry = flags & cv2.EVENT_FLAG_CTRLKEY
    if event == cv2.EVENT_LBUTTONDOWN:
        point = np.array([x, y], dtype=np.int32).reshape([1, 2])
    elif event == cv2.EVENT_RBUTTONDOWN:
        # Second zoom-layer selection
        uv = zoomed_inquiry(param["frame"], np.array([x, y]))
        point = uv.astype(np.int32).reshape([1, 2])
    else:
        return
    points.append(point)

    # Ask for a tag in a separate window
    if bypass_inquiry:
        tag = prev_tag
    else:
        tag = param["prompt"]()
        if tag is None:
            print("canceled")
            return
    prev_tag = tag
    marker_label.append(tag)
    print("added: ")
    print(point, tag)


# Draw
# TODO: move to cv2_custom
def frame_label(frame, points, marker_label):
    for inx in range(len(points)):
        point = tuple(points[inx][0])
        tag = marker_label[inx]
        cv2_draw_label(frame, int(point[0]), int(point[1]), tag, fontScale=0.8)


@click.command()
@click.option(
    "-t",
    "--tag",
    type=str,
    help="Experiment tag. Path ./tag should exist.",
)
@click.option(
    "-c", "--cam-id", type=int, help="Camera index given in file.", multiple=True
)
@click.option(
    "-r",
    "--run-id",
    type=int,
    help="Specify run index. Initial points are saved for all specified run-ids.",
    multiple=True,
)
@click.option(
    "-ss", "--start-frame", type=int, help="Start frame.", default=0, show_default=True
)
@click.option(
    "-es", "--end-frame", type=int, help="End frame.", default=-1, show_default=True
)
@click.option("-v", "--verbose", is_flag=True, help="Verbose mode.")
@click.option("-d", "--dry", is_flag=True, help="Dry run.")
def main(tag, cam_id, run_id, start_frame, end_frame, verbose, dry):
    app = QApplication(sys.argv)  # Initialize Q application

    config = br2_vision.load_config()
    config_logging(verbose)
    logger = get_script_logger(os.path.basename(__file__))
    scale = config["DIMENSION"]["scale_video"]

    if len(run_id) > 1 and start_frame != 0:
        logger.error("Start frame is only supported for single run_id.")
        sys.exit(1)

    marker_positions = MarkerPositions.from_yaml(config["PATHS"]["marker_positions"])
    keys = marker_positions.tags

    # Set prompt
    prompt = LabelPrompt(
        app,
        list(map(str, range(len(marker_positions)))),
        keys,
        "cross-section index",
        "label tag",
    )

    # Set Colors
    _N = 100
    np.random.seed(100)
    color = np.random.randint(0, 235, (100, 3)).astype(int)

    # Path
    for cid in cam_id:

        video_path = config["PATHS"]["footage_video_path"].format(tag, cid, run_id[0])
        assert os.path.exists(video_path), f"Video not found: {video_path}."
        initial_point_file = config["PATHS"]["tracing_data_path"]

        video_name = os.path.basename(video_path)

        # Capture Video
        cap = cv2.VideoCapture(video_path)
        video_length = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if start_frame == -1:
            start_frame = video_length - 1
        if end_frame == -1:
            end_frame = video_length
        if start_frame > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        ret, curr_frame = cap.read()
        curr_frame = scale_image(curr_frame, scale)

        assert start_frame < video_length

        marker_label = []
        points = []

        # First-layer Selection
        cv2.namedWindow(video_name)
        cv2.setMouseCallback(
            video_name,
            mouse_event_click_point,
            param={
                "frame": curr_frame,
                "points": points,
                "marker_label": marker_label,
                "prompt": prompt,
            },
        )
        while True:
            disp_img = curr_frame.copy()

            if len(points) > 0:
                frame_label(disp_img, points, marker_label)

            cv2.imshow(video_name, disp_img)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("c"):
                print("done")
                break
            elif key == ord("d"):
                if len(points) > 0:
                    print(f"deleted: {points[-1]} -> {marker_label[-1]}")
                    points.pop(-1)
                    marker_label.pop(-1)
            elif key == ord("h"):
                print("check")
                print(points)
                print(marker_label)
                print("")
                print("c: complete")
                print("d: delete last point")
        cv2.destroyAllWindows()

        # Load existing points and marker_label
        for rid in run_id:
            with TrackingData.initialize(
                path=initial_point_file.format(tag, rid),
                marker_positions=marker_positions,
            ) as dataset:
                for label, point in zip(marker_label, points):
                    point = tuple(point.ravel().tolist())
                    flow_queue = FlowQueue(
                        point, start_frame, end_frame, cid, label[0], label[1]
                    )
                    dataset.append(flow_queue)

    visualize(tag, cam_id, run_id, config)

    app.quit()  # Quit Q application


def visualize(tag, cam_id, run_id, config, frame=0):
    initial_point_file = config["PATHS"]["tracing_data_path"]
    working_dir = pathlib.Path(config["PATHS"]["postprocessing_path"].format(tag))

    initial_points_dir = working_dir / "initial_points"
    initial_points_dir.mkdir(parents=True, exist_ok=True)

    for rid in run_id:
        with TrackingData.load(path=initial_point_file.format(tag, rid)) as dataset:
            for cid in cam_id:
                plt.figure()
                flow_queues = dataset.get_flow_queues(camera=cid, start_frame=frame)
                points = np.array([queue.point for queue in flow_queues])  # (N, 2)
                plt.scatter(points[:, 0], points[:, 1])
                plt.title(f"frame {frame}")
                plt.savefig(
                    initial_points_dir
                    / f"initial_points_cam{cid}_run{rid}_frame{frame}.png"
                )
                plt.close("all")


if __name__ == "__main__":
    main()
