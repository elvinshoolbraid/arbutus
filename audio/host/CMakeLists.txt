# arbutus-audio: the audio process (§8). One executable, no frameworks.
add_executable(arbutus-audio main.cpp audio_host.cpp rack.cpp tape.cpp tape_store.cpp tape_io.cpp device.cpp imu_lsm6dso.cpp keys_reader.cpp keys_port.cpp keys_thread.cpp)
target_link_libraries(arbutus-audio PRIVATE arb_rtcount arb_hostlib arb_sdk arb_json ${CMAKE_DL_LIBS})
target_include_directories(arbutus-audio SYSTEM PRIVATE "${ARB_AUDIO_ROOT}/third_party/miniaudio")
target_compile_definitions(arbutus-audio PRIVATE ARB_HOST_ARCH="${ARB_ARCH}")
if(APPLE)
  target_link_libraries(arbutus-audio PRIVATE
    "-framework CoreFoundation" "-framework CoreAudio" "-framework AudioToolbox")
elseif(UNIX)
  find_package(Threads REQUIRED)
  target_link_libraries(arbutus-audio PRIVATE Threads::Threads ${CMAKE_DL_LIBS} m)
endif()
