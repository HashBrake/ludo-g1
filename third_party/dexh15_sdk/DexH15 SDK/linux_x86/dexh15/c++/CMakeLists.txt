cmake_minimum_required(VERSION 3.10)

project(DexH15_SDK_Example)

set(CMAKE_CXX_STANDARD 17)

set(CMAKE_CXX_STANDARD_REQUIRED True)

find_package(DexHandSDK REQUIRED)
if(DexHandSDK_FOUND)
    include_directories(${DexHandSDK_INCLUDE_DIRS})
endif()

find_package(OpenCV REQUIRED)
if(OpenCV_FOUND)
    include_directories(${OpenCV_INCLUDE_DIRS})
endif()

set(SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR})

file(GLOB ALL_CPP_FILES "${SOURCE_DIR}/*.cpp")

foreach(CPP_FILE ${ALL_CPP_FILES})
    get_filename_component(EXE_NAME ${CPP_FILE} NAME_WE)

    add_executable(${EXE_NAME} ${CPP_FILE})

    target_link_libraries(${EXE_NAME} ${OpenCV_LIBS} ${DEXHANDSDK_ALL_LIBS})
endforeach()
