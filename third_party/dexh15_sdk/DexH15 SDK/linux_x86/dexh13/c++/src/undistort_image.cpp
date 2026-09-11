#include <dexh13/dexh13_control.h>

#include <opencv2/opencv.hpp>
#include <sstream>
#include <thread>

int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh13::DexH13Control> dexh13 = std::make_shared<paxini::bot::dexh13::DexH13Control>();
  // 激活灵巧手
  int handy_type = dexh13->activeHandy("/dev/ttyUSB0", "/dev/video4");
  std::cout << "步骤: 激活灵巧手成功, 灵巧手类型为" << (handy_type == 1 ? "左手" : "右手") << std::endl;
  // 获取相机内参矩阵和畸变系数(分辨率为 640x480)
  std::pair<cv::Mat, cv::Mat> intrinsic_matrix_and_dist_coeffs = dexh13->getIntrinsicMatrixAndDistCoeffs(640, 480);
  std::cout << "相机内参矩阵:" << intrinsic_matrix_and_dist_coeffs.first << std::endl;
  std::cout << "畸变系数:" << intrinsic_matrix_and_dist_coeffs.second << std::endl;
  // 获取相机外参矩阵
  cv::Mat hand_eye_matrix = dexh13->getHandEyeMatrix();
  std::cout << "相机外参矩阵:" << hand_eye_matrix << std::endl;
  // 设置相机分辨率和帧率(分辨率为 640x480, 帧率为 30 fps)
  dexh13->setCameraConfig(640, 480, 30);
  // 获取当前帧功能
  cv::Mat original_image = dexh13->getFrame();
  // 保存一帧图片
  dexh13->saveImage("save_orig_image.png", original_image);
  // 图像校正（去畸变）
  cv::Mat undistorted_image = dexh13->undistortImage(original_image, intrinsic_matrix_and_dist_coeffs.first,
                                                     intrinsic_matrix_and_dist_coeffs.second);
  // 保存校正后的图像
  dexh13->saveImage("save_undistorted_image.png", undistorted_image);
  while (true) {
    cv::Mat ori_frame = dexh13->getFrame();
    cv::imshow("Original Image", ori_frame);
    cv::Mat undistort_frame = dexh13->undistortImage(ori_frame, intrinsic_matrix_and_dist_coeffs.first,
                                                     intrinsic_matrix_and_dist_coeffs.second);
    cv::imshow("Undistorted Image", undistort_frame);
    if (cv::waitKey(1) == 27) {
      break;
    }
  }
  return 0;
}