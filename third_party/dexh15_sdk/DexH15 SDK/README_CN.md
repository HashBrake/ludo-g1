# 帕西尼感知灵巧手 SDK 编程指南

[TOC]

欢迎使用[深圳帕西尼感知科技](https://www.paxini.com)提供的灵巧手系列产品， 帕西尼感知灵巧手 SDK 是一套用于控制灵巧手产品的开发工具包，它提供了一系列的 API 和工具，用于连接和控制灵巧手设备。SDK 支持多种编程语言，包括 C++ 和 Python，并提供了丰富的功能，包括设备连接、参数设置、控制模式、运行状态、目标位置控制等。


## SDK 体系目录结构

```bash
dexh15sdk/                                   # 目录
├── DexHandSDK-3.2.1-Linux.deb               # SDK 库
├── pxdex-3.2.1-cp310-cp310-linux_type.whl   # SDK Python (type为 x86_64 或 aarch64)
├── gsl.tar.gz                               # gsl 编译文件
├── eigen-3.3.7.tar.gz                       # eigen 编译文件
├── examples_type                            # 示例代码
│   ├── dexh5
│   ├── dexh13
│   └── dexh15                    
└── README_CN.md                             # readme 文档

```

## 使用说明

**推荐配置**

- 操作系统版本、最低硬件配置 Ubuntu 22.04 LTS  CPU（64-bit）RAM（8GB），free disk space（20GB）

**环境要求**

- 适用于 Paxini 灵巧手系列产品
- 开发语言 C++ 17 标准，使用 CMake >= 3.22 构建
- 适用于 Linux 系统，支持架构包括 x86_64，建议使用 Ubuntu 22.04 LTS
- 软件库版本信息 Boost 1.74.0，OpenCV 4.5.4+dfsg-9-ubuntu4，spdlog 1.9.2，fmt 8.1.1，gsl 2.7，eigen-3.3.7


## Ubuntu x86_64 环境快速安装

如何安装和配置这个工程，安装工程所需的所有依赖项，包括库、工具和其他软件包。

**下载地址**

- 系统支持 Ubuntu 22.04，支持 x86_64
release [DexHandSDK-v3.2.1-Linux](https://paxini.com/about)

**安装 SDK 依赖库**
```bash
# 依赖项(如无法一次性进行安装，可逐个安装依赖)
sudo apt update && sudo apt install -y build-essential libboost-all-dev libopencv-dev libspdlog-dev libfmt-dev libgoogle-glog-dev libopenblas-dev libdlib-dev libjsoncpp-dev libfltk1.3-dev libyaml-cpp-dev liburdfdom-dev liburdfdom-headers-dev python3-pip python3-dev cmake vim git

```
> sdk压缩包提供了 gsl 和 eigen3.3.7 tar包，可以直接解压安装

**安装 SDK**
```bash
# Linux C++ 版本安装（不区分架构）
sudo dpkg -i DexHandSDK-3.2.1-Linux.deb

# Linux x86_86 下 python 版本安装（使用 sudo 安装的话，python 代码也需要使用 sudo 运行）
python3 -m pip install pxdex-3.2.1-cp310-cp310-linux_x86_64.whl

```

## C++ API 概览

SDK 支持 DexH5、DexH13 和 GMH15 三款产品，提供丰富的灵巧手参数设置、控制模式、运行状态、目标位置控制等开发，功能如下

### DexH5 API  
> 命名空间 namespace paxini::bot::dexh5 

| DexH5Control 接口名            | 功能说明                                              |
| ----------------------------- | ------------------------------------------------ |
| DexH5Control                  | 灵巧手控制类（单手模式）                             |
| connectDevice                 | 连接设备                                           |
| disconnectDevice              | 连接断开                                           |
| isConnected                   | 连接状态                                           |
| setMotorMaxCurrent            | 设置 -- 自定义电机最大电流                           |
| setMotorHoldTorque            | 设置 -- 设置维持力矩和维持时间                       |
| setJointPositionsDegree       | 控制 -- 手指关节角度控制                             |
| setJointPositionsRadian       | 控制 -- 手指关节弧度控制                             |
| calibrateTactile              | 标定 -- 灵巧手触觉传感器（校准操作）                   |
| isMotorEnabled                | 查询 -- 手指电机使能状态                             |
| getJointPositionsDegree       | 查询 -- 获取当前手指关节角度                          |
| getJointPositionsRadian       | 查询 -- 获取当前手指关节弧度                          |
| getMotorCurrentAndTemperature | 查询 -- 获取电机当前电流和温度                        |
| getTactileResultantForce      | 查询 -- 手指触觉传感器数据合力和温度                   |
| getTactileDistributedForce    | 查询 -- 手指触觉传感器数据分布力和温度                 |
| getVersion                    | 查询 -- SDK和固件的版本信息                          |
| getMotorPosition              | 查询 -- 获取电机当前位置                             |
| getRuntimeStatus              | 查询 -- 获取灵巧手设备运行状态                        |
| isFault                       | 故障 -- 手指故障状态                                |
| getFaultCode                  | 故障 -- 当前故障原因                                |
| clearFaultCode                | 故障 -- 清除故障码                                  |

| DexH5Kinematic 接口名          | 功能说明                                           |
| ----------------------------- | ------------------------------------------------ |
| DexH5Kinematic                | 灵巧手运动学类（单手模式）                            |
| getSDKVersion                 | 查询 SDK 版本                                      |
| inverseKinematicParse         | 逆运动学解析                                        |
| forwardKinematicParse         | 正运动学解析                                        |

### DexH13 API  
> 命名空间 namespace paxini::bot::dexh13 

| DexH13Control 功能名              | 说明                                     |
| :------------------------------ | :--------------------------------------|
| DexH13Control                   | 灵巧手控制类（单手模式）                   |
| scanDevice                      | 扫描设备（仅 ethercat 可用）               |
| connectHandByEthercat           | 扫描设备（仅 ethercat 可用）               |
| activeHandy                     | 连接设备                                 |
| disconnectHandy                 | 连接断开                                 |
| isConnectHandy                  | 连接状态（仅 modbus 协议可用）              |
| initMotorPosition               | 初始化电机位置                           |
| setMotorMaxCurrent              | 设置 -- 自定义电机最大电流                 |
| setMotorControlMode             | 设置 -- 自定义电机控制模式                 |
| setMotorMaxSpeed                | 设置 -- 自定义电机最大速度                 |
| enableMotor                     | 使能 -- 手指电机使能                      |
| disableMotor                    | 使能 -- 手指电机下使能                    |
| isMotorEnabled                  | 使能 -- 手指电机使能状态                   |
| setJointPositionsAngle          | 控制 -- 手指关节角度控制                   |
| setJointPositionsRadian         | 控制 -- 手指关节弧度控制                   |
| setMotorTargetCurrent           | 控制 -- 电机目标电流                      |
| getMotorActualCurrent           | 查询 -- 电机实际电流                      |
| setMotorTargetSpeed             | 控制 -- 电机目标速度                      |
| getMotorActualSpeed             | 查询 -- 电机实际速度                      |
| calibrateSensor                 | 标定 -- 灵巧手触觉传感器（校准操作）         |
| getJointPositionsAngle          | 查询 -- 获取手指关节角度                   |
| getJointPositionsRadian         | 查询 -- 获取手指关节弧度                   |
| getMotorControlMode             | 查询 -- 控制模式                          |
| getFingerTactile                | 查询 -- 手指触觉传感器合力信息              |
| getFingerTactileDetail          | 查询 -- 手指触觉传感器阵列力详细信息         |
| getTemperature                  | 查询 -- 传感器温度数据                     |
| getAllTactileData               | 查询 -- 全部数据（包括合力、阵列力和温度）     |
| isFault                         | 故障 -- 手指故障状态                       |
| getFaultCode                    | 故障 -- 当前故障原因                       |
| clearFaultCode                  | 故障 -- 清除故障码                         |
| getFrame                        | 图像 -- 当前相机一帧图像                    |
| setCameraConfig                 | 图像 -- 相机参数，包括分辨率和帧率           |
| undistortImage                  | 图像 -- 校正，去畸变的图像                  |
| getIntrinsicMatrixAndDistCoeffs | 图像 -- 获取相机内参矩阵和畸变系数           |
| getHandEyeMatrix                | 图像 -- 获取手眼相机外标定参数              |
| showImage                       | 图像 -- 显示一帧图片                       |
| saveImage                       | 图像 -- 保存图片到指定路径                  |
| getSDKVersion                   | 版本 -- SDK的版本信息                     |
| getFirmwareVersion              | 版本 -- 灵巧手固件版本信息                 |
| getPressPositionForce           | 查询 -- 按压位置力信息                     |
| upgradeMainboard                | 升级 -- 主控固件升级                       |
| upgradeDriveboard               | 升级 -- 电驱固件升级                       |
| getUpgradeInfo                  | 升级 -- 获取升级进度信息                    |

| DexH13Kinematic 接口名           | 功能说明                                           |
| ------------------------------- | ------------------------------------------------ |
| DexH13Kinematic                 | 灵巧手运动学类                                      |
| getSDKVersion                   | 查询 SDK 版本                                      |
| inverseKinematicParse           | 逆运动学解析                                        |
| forwardKinematicParse           | 正运动学解析                                        |

| DexH13ForceControl 接口名        | 功能说明                                           |
| ------------------------------- | ------------------------------------------------ |
| DexH13ForceControl              | 灵巧手力控类                                       |
| getSDKVersion                   | 查询 SDK 版本                                      |
| setForceCtlParam                | 力控参数配置                                        |
| computeSerialTargetCommand      | 计算下次目标弧度值                                   |

### GMH15 API  
> 命名空间 namespace paxini::bot::dexh15

| DexH15Control 功能名                 | 说明                                           |
| :-----------------------------------| :---------------------------------------------|
| DexH15Control                       | 灵巧手控制类                                    |
| connectModbusAuto                   | 连接 -- 自动连接                                |
| openModbusDevice                    | 连接 -- 打开 Modbus 串口                        |
| scanModbusDevices                   | 连接 -- 扫描 Modbus 从站地址                     |
| initModbusDevice                    | 连接 -- 初始化从站设备                           |
| disconnectModbus                    | 连接 -- 关闭 Modbus 串口                        |
| isModbusDeviceConnected             | 查询 -- 连接状态                                |
| setModbusBaudrate                   | 设置 -- 从站波特率                              |
| getModbusBaudrate                   | 获取 -- 从站波特率                              |
| setModbusDeviceAddress              | 设置 -- 从站地址                                |
| openUsbDevice                       | 连接 -- 打开 USB 设备                           |
| disconnectUsb                       | 连接 -- 关闭 USB 设备                            |
| setMotorControlMode                 | 设置 -- 电机控制模式                             |
| getHandType                         | 获取 -- 左右手类型                               |
| setMotorMaxCurrent                  | 设置 -- 电机最大电流                              | 
| getMotorMaxCurrentConfig            | 获取 -- 电机最大电流设置                          |
| setMotorMaxVelocity                 | 设置 -- 电机最大速度                              |
| getMotorMaxVelocityConfig           | 获取 -- 电机最大速度配置                          |
| enableMotor                         | 控制 -- 使能电机                                 |
| disableMotor                        | 控制 -- 下使能电机                                |
| isMotorEnabled                      | 查询 -- 电机是否使能                              |
| setJointPositionsAngle              | 设置 -- 单个手指关节角度                           |
| setJointPositionsAngle              | 设置 -- 所有手指关节角度                           |
| calculateRealAngle                  | 查询 -- 归一角度转真实角度                          |
| calculateNormalizeAngle             | 查询 -- 真实角度转归一角度                          |
| setMotorTargetCurrent               | 设置 -- 所有电机最大电流                            |
| setMotorTargetCurrent               | 设置 -- 单个电机最大电流                            |
| getMotorActualCurrent               | 获取 -- 所有电机当前电流                            |
| setMotorTargetVelocity              | 设置 -- 所有电机目标速度                            |
| setMotorTargetVelocity              | 设置 -- 单个电机目标速度                            |
| getMotorActualVelocity              | 获取 -- 所有电机当前速度                            |
| calibrateSensor                     | 标定 -- 传感器标定                                 |
| getJointPositionsAngle              | 获取 -- 所有手指归一角度                            |
| getJointPositionsAngle              | 获取 -- 单个手指归一角度                            |
| getMotorControlMode                 | 获取 -- 电机控制模式                               |
| getFingerResultantForce             | 获取 -- 传感器合力                                 |
| getFingerDistributedForce           | 获取 -- 传感器分布力                               |
| getTemperature                      | 获取 -- 传感器温度                                |
| getFingerTactileData                | 获取 -- 单根手指的所有触觉数据                      |
| getAllTactileData                   | 获取 -- 所有手指的所有触觉数据                      |
| checkHandStatus                     | 查询 -- 灵巧手状态                                |
| getFaultCode                        | 获取 -- 电机错误码                                |
| clearFaultCode                      | 查询 -- 清除电机错误码                             |
| getFirmwareVersion                  | 查询 -- 获取固件版本                               |
| getSDKVersion                       | 获取 -- SDK 版本                                  |
| initMotorPosition                   | 控制 -- 初始化电机位置                             |
| setMotorTargetPosition              | 设置 -- 所有电机目标位置                            |
| setMotorTargetPosition              | 设置 -- 单个电机目标位置                            |
| getMotorPosition                    | 获取 -- 电机位置                                  |
| getPressPositionForce               | 获取 -- 按压位置力                                |
| getDeviceSN                         | 获取 -- 设备 SN 号                                |
| getHardwareVersion                  | 获取 -- 硬件版本                                  |
| getModbusConfig                     | 获取 -- Modbus 配置信息                           |
| getDeviceInfo                       | 获取 -- 从站设备信息                                |
| getJointMagneticEncoder             | 获取 -- 磁编数据                                  |
| getActionPosition                   | 查询 -- 加载动作信息                                |
| runAction                           | 控制 -- 运行动作                                   |
| saveCustomizedAction                | 保存 -- 用当前位置保存用户自定义动作                  |
| saveCustomizedAction                | 保存 -- 以电机的形式保存用户自定义动作                |
| saveCustomizedAction                | 保存 - 以关节弧度的形式保存用户自定义动作              |
| setCommonForceCtlParam              | 设置 -- 普通力控参数                               |
| computeCommonForceTargetCommand     | 计算 -- 普通力控的下一个目标位置                     |
| setTangentialForceCtlParam          | 设置 -- 切向力力控参数                             |
| computeTangentialForceTargetCommand | 计算 -- 切向力力控的下一次目标位置                    |

| DexH15Kinematic 接口名               | 功能说明                                           |
| ------------------------------------|-------------------------------------------------- |
| DexH15Kinematic                     | 灵巧手运动学类                                      |
| getSDKVersion                       | 查询 SDK 版本                                      |
| inverseKinematicParse               | 逆运动学解析                                        |
| forwardKinematicParse               | 正运动学解析                                        |
| calculateRealAngle                  | 传入归一值计算真实值                                  |
| calculateNormalizeAngle             | 传入真实值计算归一值                                  |
 
## 详细接口描述
请参考 SDK 技术文档，包括每个函数的名称、参数、返回值、功能描述。

## example 运行
示例代码，请参考这个工程的 example 指南，运行程序。


## 联系方式

如果您使用过程中有问题，或者您的工程需要特殊定制请联系电子邮件地址。

邮箱：mkt@paxini.com

