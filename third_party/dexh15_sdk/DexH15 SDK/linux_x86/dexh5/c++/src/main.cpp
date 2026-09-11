#include <iostream>

#include "dexh5_example.h"

int main(int argc, char** argv) {
  if (argc < 2) {
    std::cerr
        << "用法: " << argv[0]
        << " <输入文件> [--example_num 示例编号(1:版本信息 2:执行手势 3:获取传感器数据 4:逆运动学解析 5:正运动学解析)]"
        << "--hand_port 灵巧手串口号(/dev/ttyUSB0) " << std::endl;
    return 1;
  }
  std::string example_num;
  std::string hand_port;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--example_num" && i + 1 < argc) {
      example_num = argv[++i];
    }
    if (arg == "--hand_port" && i + 1 < argc) {
      hand_port = argv[++i];
    }
  }
  int num = std::stoi(example_num);
  DexH5Example* dexh5_example = new DexH5Example(hand_port);
  // 查询 DexH5 SDK 的版本信息
  if (num == 1) {
    dexh5_example->getVersion();
  }
  // 执行手势
  if (num == 2) {
    dexh5_example->runGestures();
  }
  // 获取传感器数据
  if (num == 3) {
    dexh5_example->getFingerTactile();
  }
  // 逆运动学解析
  if (num == 4) {
    dexh5_example->inverseKinematicParse();
  }
  // 正运动学解析
  if (num == 5) {
    dexh5_example->forwardKinematicParse();
  }

  delete dexh5_example;
  return 0;
}