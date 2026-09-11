import pxdex.dh15 as hand_packge

# 相机接口使用参考示例
if __name__ == "__main__":
    """
    步骤示例：
        1. 创建相机控制实例[必选]
        2. 连接相机【必选】
        3. 设置相机参数
        4. 获取一帧图片 
        5. 释放相机接口【必选】
    """
    # 1. 创建相机控制实例
    camera_control = hand_packge.DexH15Camera()

    # 相机串口，需要根据实际情况调整
    camera_port = "/dev/video2"
    # 2. 连接相机
    camera_control.connectCameraDevice(camera_port)
    print("connect camera device success")

    # 3. 设置相机参数
    if camera_control.setCameraConfig(640, 480, 30):
        print("设置相机参数成功")
    else:
        print("设置相机参数失败")

    # 4. 获取一帧图片
    frame = camera_control.getFrame()
    camera_control.showImage("image", frame)

    # 5. 释放相机接口
    camera_control.releaseCameraDevice()
    print("release camera device success")


