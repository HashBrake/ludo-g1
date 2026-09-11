/**
 ******************************************************************************
 * @file: dexh5_example.h
 * @brief: DexH5 SDK 示例
 * @version: 0.0.1
 * @date: 2025-10-23
 * @copyright: Copyright (c) 2025 Paxini Robotics
 * @author: paxini
 * @details:
 ******************************************************************************
 */

#pragma once
#include <dexh5/dexh5_control.h>
#include <dexh5/dexh5_define.h>
#include <dexh5/dexh5_kinematic.h>

#include <string>

/**
 * @class DexH5Example
 * @brief DexH5 SDK 关键功能的使用教程示例
 * @note
 * @see
 */
class DexH5Example {
 public:
  DexH5Example(const std::string& hand_port_num);
  ~DexH5Example();

  /**
   * @brief 查询 DexH5 SDK 的版本信息
   * @details DexH5 SDK 的版本信息包括 SDK 的软件版本信息和固件版本信息
   *          步骤：
   *          (1) 连接灵巧手
   *          (2) 查询灵巧手连接状态
   *          (3) 查询版本信息
   *          (4) 断开灵巧手连接
   * @return void
   */
  void getVersion();

  /**
   * @brief 获取传感器数据
   * @details 步骤：
   *          (1) 激活灵巧手
   *          (2) 查询灵巧手连接状态
   *          (3) 标定灵巧手触觉传感器（校准操作，可选）
   *          (4) 获取传感器信息(按住指尖传感器，数值才有变化)
   *          (5) 断开灵巧手连接
   *          PS：example 中采用死循环来持续获取传感器信息，第(4)步没在 example 中体现
   *              如果要中断 example 的执行，请在命令行窗口按 ctrl + C
   * @return void
   */
  void getFingerTactile();

  /**
   * @brief 执行多个手势
   * @details 无
   * @return void
   */
  void runGestures();

  /**
   * @brief 逆运动学解析，通过弧度值获取电机值
   * @details 无
   * @return void
   */
  void inverseKinematicParse();

  /**
   * @brief 正运动学，通过电机值获取弧度值
   * @details 无
   * @return void
   */
  void forwardKinematicParse();

  /**
   * @brief 打印关节角度
   * @details 手指关节数组 5 个关节的控制顺序：
   *        - index mcp 食指角度: (0, 77.5°)
   *        - middle mcp 中指角度: (0, 77.5°)
   *        - ring mcp 环指角度: (0, 77.5°)
   *        - thumb cmc 拇指掌心角度: (0, 73°)
   *        - thumb mcp 拇指角度: (0, 43.6°)
   * @return void
   */
  void printJointDegree(const std::vector<double>& degrees);

  /**
   * @brief 打印触觉传感器信息
   * @details 触觉传感器信息包含三个维度的力： x, y, z
   *          x:x轴方向的力
   *          y:y轴方向的力
   *          z:z轴方向的力
   * @return void
   */
  void printForcePoints(std::vector<paxini::bot::dexh5::H5ForcePoint>& force_points);

 private:
  /**
   * 灵巧手串口，例如 /dev/ttyUSB*
   * 可用命令 ls /dev/ttyUSB*，来查看
   */
  std::string hand_port_num;
};