import time

from pxdex.dh5 import DexH5Control

def main():
    print("--- ----- --- Dexterous Hand Test Start --- ----- --- ")

    # Activate and enable the hand
    dexh5 = DexH5Control()
    dexh5.connectDevice("/dev/ttyUSB0")
    time.sleep(1)

    # ###sensor calibration
    # calibrateTactile = dexh5.calibrateTactile()
    # print("calibrateTactile:",calibrateTactile)

    ###  Inquiry Frequency
    hz = 10 

    while True:
        getTactileResultantForce = dexh5.getTactileResultantForce()
        print("--- ----- --- TactileResultantForce data:")
        for tac in getTactileResultantForce:
            if isinstance(tac, list):
                for sensorInfo in tac:
                    print("Finger_name: {0}, sensor_id {1}: , x: {2}, y: {3}, z: {4} ".format(sensorInfo.finger_name,sensorInfo.sensor_id,sensorInfo.x, sensorInfo.y, sensorInfo.z))
            else:
                print("getTactileResultantForce HReturnCode:", tac)

        time.sleep(1 / hz)


if __name__ == "__main__":
    main()