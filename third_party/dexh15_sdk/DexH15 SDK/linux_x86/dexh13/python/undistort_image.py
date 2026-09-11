import cv2
from pxdex.dh13 import DexH13Control

if __name__ == "__main__":
    control = DexH13Control()
    # 连接灵巧手
    handy_type = control.activeHandy('/dev/ttyUSB0', '/dev/video4')
    print(f"激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
    # 获取相机内参矩阵和畸变系数(分辨率为 640x480)
    control.setCameraConfig(640, 480, 30)
    # 获取一帧图像
    frame = control.getFrame()
    # 获取相机内参矩阵和畸变系数(分辨率为 640x480)
    intrinsic_640_480, coeffs_640_480 = control.getIntrinsicMatrixAndDistCoeffs(640, 480)
    # 获取相机外参矩阵
    hand_eye_matrix = control.getHandEyeMatrix()
    print("相机外参矩阵: ", hand_eye_matrix)
    # 图像校正（去畸变）
    undistort_frame_640_480 = control.undistortImage(frame, intrinsic_640_480, coeffs_640_480)
    control.saveImage("undistort_frame_640_480.png", undistort_frame_640_480)  # save the image
    while True:
        ori_frame = control.getFrame()
        cv2.imshow("ori_frame", ori_frame)
        undistort_frame = control.undistortImage(ori_frame, intrinsic_640_480, coeffs_640_480)
        cv2.imshow("undistort_frame", undistort_frame)
        key = cv2.waitKey(1)
        if key == ord('q'):
            break
    # 断开灵巧手连接
    is_success = control.disconnectHandy()
    print(f"断开灵巧手连接{'成功' if is_success else '失败'}")
