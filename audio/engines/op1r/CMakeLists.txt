# The OP-1 adapter boxes: op1r's effects, unmodified, as Arbutus boxes (#1230).
# README.md says how they work and how to add one.

set(OP1R_SRC "${ARB_AUDIO_ROOT}/third_party/op1r-src")
set(OP1R_ADAPTER "${CMAKE_CURRENT_SOURCE_DIR}/adapter")

# What every op1r box needs past add_arbutus_engine(): the host harness source,
# op1r's headers with OP1_HOST (its PC build), and the box's own src/ ahead of
# them for any stub a plugin's include needs (none since #1255: reverb.c's
# kali_geom.h is op1r's own, vendored beside it).
function(op1r_box target)
  target_sources(${target} PRIVATE "${OP1R_ADAPTER}/op1r_host.c")
  target_compile_definitions(${target} PRIVATE OP1_HOST)
  target_include_directories(${target} PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src" "${OP1R_ADAPTER}" "${OP1R_SRC}/synth_sdk")
endfunction()

# The vendored files are op1r's, byte for byte (VENDORED.md); the public
# export's HELD.txt names the files it held back (#1299; absent here).
add_test(NAME op1r_vendored
         COMMAND ${CMAKE_COMMAND} -DDIR=${OP1R_SRC}
                 -DHELD=${ARB_AUDIO_ROOT}/../HELD.txt
                 -DREL=audio/third_party/op1r-src
                 -P "${CMAKE_CURRENT_SOURCE_DIR}/check_vendored.cmake")

add_subdirectory(op1r-verb)
