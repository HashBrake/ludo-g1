#include <dexh15/dexh15_camera.h>

#include <iostream>
#include <opencv2/opencv.hpp>

/**
 *  @brief 相机功能
 *  @details 步骤：
 *               (1) 打开相机串口
 *               (2) 设置分辨率和帧率
 *               (3) 获取一帧图像
 *               (4) 显示图像
 *               (5) 释放相机串口
 */
int main(int argc, char **argv) {
  std::shared_ptr<paxini::bot::dexh15::DexH15Camera> dexh15_camera =
      std::make_shared<paxini::bot::dexh15::DexH15Camera>();
  // 相机串口
  std::string camera_port_num = "/dev/video4";
  try {
    // 打开相机串口
    dexh15_camera->connectCameraDevice(camera_port_num);
    // 设置分辨率和帧率
    if (!dexh15_camera->setCameraConfig(640, 480, 30)) {
      std::cout << "设置相机参数失败" << std::endl;
    }
    std::cout << "设置相机参数成功" << std::endl;
    // 获取一帧图像
    cv::Mat frame = dexh15_camera->getFrame();
    // 显示图像
    dexh15_camera->showImage("img", frame);
    // 释放相机串口
    dexh15_camera->releaseCameraDevice();
    std::cout << "释放相机串口" << std::endl;
  } catch (...) {
  }

  return 0;
}