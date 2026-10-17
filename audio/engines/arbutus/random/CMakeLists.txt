add_arbutus_engine(random SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/random")

# Goldens frozen from this box; README.md says what was checked.
# 1/16 at 120 BPM, SLEW 0.4, RANGE 0.6, host seed 11 (SEED 0 = HOST).
add_test(NAME random_golden COMMAND arbutus-run "${_box}"
         --len 96000 --sr 48000 --frames 128 --tempo 120 --seed 11
         --set rate=0.7738 --set slew=0.4 --set range=0.6
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/random_sync.npy" --tol -100)
# Free 4.8 Hz at 512-frame blocks, SEED 42 overriding the host seed.
add_test(NAME random_golden_seed COMMAND arbutus-run "${_box}"
         --len 96000 --sr 48000 --frames 512 --seed 5
         --set rate=-0.6667 --set seed=42
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/random_seed.npy" --tol -100)

add_test(NAME rt_guard_random COMMAND arbutus-run "${_box}" --rt-guard
         --len 96000 --sr 48000 --set rate=-1 --set slew=0.5)

add_executable(test_random_format tests/test_format.cpp)
target_link_libraries(test_random_format PRIVATE arb_sdk arb_hostlib)
add_dependencies(test_random_format random_box)
add_test(NAME random_format COMMAND test_random_format "${_box}")
