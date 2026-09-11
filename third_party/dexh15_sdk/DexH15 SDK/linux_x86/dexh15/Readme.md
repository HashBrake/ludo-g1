# 帕西尼感知灵巧手 SDK 示例代码

## 灵巧手 SDK 示例代码目录结构
``` tree -L 4
.
├── dexh15
│   ├── c++                                      # dexh15 SDK C++ 示例代码
│   │   ├── CMakeLists.txt
│   │   ├── connect_modbus_auto.cpp              # Modbus 自动连接
│   │   ├── fault_diagnosis.cpp                  # 故障诊断
│   │   ├── finger_tactile.cpp                   # 读取传感器
│   │   ├── normal_force_control.cpp             # 法向力力位混合控制模式
│   │   ├── open_modbus_device.cpp               # Modbus 手动连接
│   │   ├── open_usb_device.cpp                  # USB 直连
│   │   ├── position_control.cpp                 # 位置模式
│   │   └── run_action.cpp                       # 执行预定义动作
│   │   └── camera.cpp                           # 相机功能
│   └── Readme.md
```

## 准备工作
### 确认设备连接接口，并添加权限
```bash
ls /dev/ttyUSB*                 # 查询 Modbus 串口设备名
ls /dev/ttyACM*                 # 查询 USB 串口设备名
ls /dev/video*                  # 查询摄像头串口设备名
                                # 通过拔插摄像头 USB 接口操作确认接口名，比如拔出再插上后多出两个设备节点 /dev/video2、 /dev/video3
                                # 运行文件中请使用 /dev/video2（出现的第一个视频流节点）
sudo chmod 666 /dev/ttyUSB*     # 查询到的设备添加权限
sudo chmod 666 /dev/video*      # 查询到的设备添加权限
sudo chmod 666 /dev/ttyACM*     # 查询到的设备添加权限
```

## dexh15 SDK 示例
### c++ 示例
#### 编译
```bash
cd {dexh15_example_dir}         # “{dexh15_example_dir}“为 dexh15 样例文件所在目录，根据实际情况修改
mkdir build
cd build
cmake ..
make

```
#### 运行
```bash
# Modbus 自动连接
./connect_modbus_auto
# Modbus 手动连接
./open_modbus_device
# USB 直连
./open_usb_device
# 故障诊断
./fault_diagnosis
# 读取传感器
./finger_tactile
# 法向力力位混合控制模式
./normal_force_control
# 位置模式
./position_control
# 执行预定义动作
./run_action
# 相机功能
./camera
```

## 常见问题
### 问题一：串口权限不足
现象：连接时提示 Permission denied: '/dev/ttyUSB0'

解决方案：
```bash
# 通过插拔的方式确认设备是否存在
ls /dev/ttyUSB*
# 临时授权查询到的设备（重启后失效），以 /dev/ttyUSB0 为例
sudo chmod 666 /dev/ttyUSB0
# 永久授权（推荐）, 注销后重新登录生效
sudo usermod -aG dialout $USER
```
### 问题二：找不到 USB 设备
现象：ls /dev/ttyUSB* 无输出
解决方案：
```bash
# 检查 USB 设备是否被系统识别
lsusb
# 查看内核日志确认连接状态
dmesg | tail -20
# 加载 USB 串口驱动（若未自动加载）
sudo modprobe cp210x    # Silicon Labs 芯片
sudo modprobe ch341     # CH340/CH341 芯片
```
### 问题三：接口 scanModbusDevices 扫描超时或返回空列表
| 可能原因                    | 处理方式                                |
| :-------------------------- | :-------------------------------------- |
| 波特率不匹配                | 改用 connectModbusAuto() 自动尝试波特率 |
| RS-485 接线错误（A/B 反接） | 检查 A+/B- 接线极性                     |
| 设备未上电                  | 检查灵巧手电源状态                      |
| 从站 ID 冲突                | 确认总线上各设备 ID 唯一                |