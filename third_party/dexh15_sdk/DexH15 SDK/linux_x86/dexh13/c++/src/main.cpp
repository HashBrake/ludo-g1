#include <iostream>

#include "dexh13_example.h"

int main(int argc, char** argv) {
  if (argc < 6) {
    std::cerr << "用法: " << argv[0] << " <输入文件> "
              << "--example_num 示例编号(1:版本信息 2:角度控制 3:弧度控制 4:手势执行 5:速度模式 6:图像校正 "
                 "7:传感器数据 8:正运动学解析 9:逆运动学解析) "
              << "--hand_port 灵巧手串口号(/dev/ttyUSB0) "
              << "--camera_port 摄像头串口号(/dev/video0)" << std::endl;
    return 1;
  }
  std::string example_num;
  std::string hand_port_num;
  std::string camera_port_num;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--example_num" && i + 1 < argc) {
      example_num = argv[++i];
    }
    if (arg == "--hand_port" && i + 1 < argc) {
      hand_port_num = argv[++i];
    }
    if (arg == "--camera_port" && i + 1 < argc) {
      camera_port_num = argv[++i];
    }
  }
  int num = std::stoi(example_num);

  DexH13Example* dexh13_example = new DexH13Example(hand_port_num, camera_port_num);
  // 查询 DexH13 SDK 的版本信息
  if (num == 1) {
    dexh13_example->getVersion();
  }
  // 角度控制
  if (num == 2) {
    dexh13_example->setJointAngle();
  }
  // 弧度控制
  if (num == 3) {
    dexh13_example->setJointRadian();
  }
  // 手势执行
  if (num == 4) {
    dexh13_example->runGestures();
  }
  // 速度模式
  if (num == 5) {
    dexh13_example->runSpeedControlMode();
  }
  // 图像校正
  if (num == 6) {
    dexh13_example->undistortImage();
  }
  // 传感器数据
  if (num == 7) {
    dexh13_example->getFingerTactile();
  }
  // 正运动学解析
  if (num == 8) {
    dexh13_example->forwardKinematicParse();
  }
  // 逆运动学解析
  if (num == 9) {
    dexh13_example->inverseKinematicParse();
  }
  delete dexh13_example;
  return 0;
}