add_arbutus_engine(motion SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/motion")
set(_motion "${CMAKE_CURRENT_SOURCE_DIR}/tests/motion.npy")

# Frozen from this box; README.md says what was checked. The feed is
# tests/make_motion.py's: flat, tilted, a tap, spun, a tremor.
add_test(NAME motion_golden COMMAND arbutus-run "${_box}"
         --motion "${_motion}" --len 48000 --sr 48000 --frames 128 --set smooth=20
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/motion.npy" --tol -100)
# No IMU: all four outputs are exactly 0 (a golden of zeros).
add_test(NAME motion_golden_none COMMAND arbutus-run "${_box}"
         --len 24000 --sr 48000 --frames 128
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/motion_none.npy" --tol -300)
# #598: flat, the sensor gone (all-zero motion), flat again. Nothing moved, so
# all four outputs stay exactly 0 -- the edge is not a SHAKE (motion_none's
# zeros are the golden).
add_test(NAME motion_dropout COMMAND arbutus-run "${_box}"
         --motion "${CMAKE_CURRENT_SOURCE_DIR}/tests/dropout.npy" --len 24000 --sr 48000 --frames 128
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/motion_none.npy" --tol -300)

add_test(NAME rt_guard_motion COMMAND arbutus-run "${_box}" --rt-guard
         --motion "${_motion}" --len 48000 --sr 48000 --frames 128)

add_executable(test_motion_format tests/test_format.cpp)
target_link_libraries(test_motion_format PRIVATE arb_sdk arb_hostlib)
add_dependencies(test_motion_format motion_box)
add_test(NAME motion_format COMMAND test_motion_format "${_box}")
