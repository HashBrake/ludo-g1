# 帕西尼感知灵巧手 SDK 示例代码

## 灵巧手 SDK 示例代码目录结构
``` tree -L 4
.
├── dexh5                                  # dexh5 SDK 示例代码
│   ├── c++                                # dexh5 SDK C++ 示例代码
│   │   ├── CMakeLists.txt
│   │   ├── include
│   │   │   └── dexh5_example.h
│   │   └── src
│   │       ├── dexh5_example.cpp
│   │       └── main.cpp
│   ├── python                             # dexh5 SDK Python 示例代码
│   │   ├── dexh5_joint_angle_motion.py
│   │   ├── dexh5_sensor_info.py
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

## dexh5 SDK 示例
### c++ 示例
#### 编译
```bash
cd {dexh5_example_dir}         # “{dexh5_example_dir}“为dexh5样例文件所在目录，根据实际情况修改
mkdir build
cd build
cmake ..
make
```
#### 运行
```bash
# example_num 示例序号 1:版本信息 2:执行手势 3:获取传感器数据 4:逆运动学解析 5:正运动学解析
# hand_port 灵巧手串口号(/dev/ttyUSB0)
./main --example_num 1 --hand_port /dev/ttyUSB0                                 
```
### python 示例
#### 运行
```bash
cd {dexh5_example_dir} 
python3 dexh5_joint_angle_motion.py                   ##执行py程序            
```





