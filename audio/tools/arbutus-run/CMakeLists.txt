add_executable(arbutus-run main.cpp)
target_link_libraries(arbutus-run PRIVATE arb_rtcount arb_hostlib arb_sdk arb_json ${CMAKE_DL_LIBS})
target_compile_definitions(arbutus-run PRIVATE ARB_HOST_ARCH="${ARB_ARCH}")
