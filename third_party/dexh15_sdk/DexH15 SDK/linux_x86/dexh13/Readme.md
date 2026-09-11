# 帕西尼感知灵巧手 SDK 示例代码

## 灵巧手 SDK 示例代码目录结构
``` tree -L 4
.
├── dexh13                                  # dexh13 SDK 示例代码
│   ├── c++                                 # dexh13 SDK C++ 示例代码
│   │   ├── CMakeLists.txt
│   │   ├── include
│   │   │   └── dexh13_example.h
│   │   └── src
│   │       ├── dexh13_example.cpp          # 各功能详细示例
│   │       ├── finger_tactile.cpp          # 传感器数据简易示例
│   │       ├── force_control.cpp           # 位置模式+力控简易示例
│   │       ├── position_control.cpp        # 位置模式简易示例
│   │       ├── speed_force_control.cpp     # 速度模式+力控简易示例
│   │       ├── undistort_image.cpp         # 图像校正简易示例
│   │       ├── upgrade_mainboard.cpp       # 升级主控固件版本简易示例
│   │       ├── upgrade_driveboard.cpp      # 升级电驱固件版本简易示例
│   │       └── main.cpp
│   ├── python                              # dexh13 SDK Python 示例代码
│   │   ├── dexh13_example.py               # 各功能详细示例
│   │   ├── finger_tactile.py               # 传感器数据简易示例
│   │   ├── force_control.py                # 位置模式+力控简易示例
│   │   ├── position_control.py             # 位置模式简易示例
│   │   ├── undistort_image.py              # 图像校正简易示例
│   │   ├── upgrade_driveboard.py           # 升级电驱固件版本简易示例
│   │   ├── upgrade_mainboard.py            # 升级主控固件版本简易示例
│   │   ├── main.py
│   └── Readme.md
```

## 确认设备连接接口，并添加权限
```bash
ls /dev/ttyUSB*                 # 查询串口设备名
ls /dev/video*                  # 查询摄像头接口名
                                # 通过拔插摄像头USB接口操作确认接口名，比如拔出再插上后多出两个设备节点/dev/video2、/dev/video3
                                # 运行文件中请使用/dev/video2（出现的第一个视频流节点）
sudo chmod 666 /dev/ttyUSB*     # 查询到的设备添加权限
sudo chmod 666 /dev/video*      # 查询到的设备添加权限
```

## dexh13 SDK 示例
### c++ 示例
#### 编译
```bash
cd {dexh13_example_dir}         # “{dexh13_example_dir}“为dexh13样例文件所在目录，根据实际情况修改
mkdir build
cd build
cmake ..
make
```
#### 运行
```bash
# example_num 示例序号 1:版本信息 2:角度控制 3:弧度控制 4:手势执行 5:速度模式 6:图像校正 7:传感器数据 8:正运动学解析 9:逆运动学解析
# hand_port 灵巧手串口号(/dev/ttyUSB0)
# camera_port 摄像头串口号(/dev/video0)
./main --example_num 4 --hand_port /dev/ttyUSB0 --camera_port /dev/video0   
# 传感器数据
./finger_tactile
# 位置模式+力控
./force_control
# 位置模式
./position_control
# 速度模式+力控
./speed_force_control
# 图像校正
./undistort_image
# 升级主控固件，主控板固件文件与upgrade_mainboard的路径保持一致
# 根据实际情况，替换代码里主控板固件文件名
./upgrade_mainboard
# 升级电驱固件，电驱固件文件与upgrade_driveboard的路径保持一致
# 根据实际情况，替换代码里电驱固件文件名
./upgrade_driveboard
```
### python 示例
#### 环境配置
``` bash
pip3 install opencv-python
```
#### 运行
```bash
# example_num 示例序号 1:版本信息 2:角度控制 3:弧度控制 4:手势执行 5:速度模式 6:图像校正 7:传感器数据 
# hand_port 灵巧手串口号(/dev/ttyUSB0)
# camera_port 摄像头串口号(/dev/video0)
python3 main.py --example_num 4 --hand_port /dev/ttyUSB0 --camera_port /dev/video0
# 传感器数据
python3 finger_tactile.py
# 位置模式+力控
python3 force_control.py
# 位置模式
python3 position_control.py
# 图像校正
python3 undistort_image.py
# 升级主控固件，主控板固件文件与upgrade_mainboard.py的路径保持一致
# 根据实际情况，替换代码里主控板固件文件名
python3 upgrade_mainboard.py
# 升级电驱固件，电驱固件文件与upgrade_driveboard.py的路径保持一致
# 根据实际情况，替换代码里电驱固件文件名
python3 upgrade_driveboard.py
```
