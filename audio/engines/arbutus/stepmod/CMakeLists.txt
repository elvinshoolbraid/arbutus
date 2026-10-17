add_arbutus_engine(stepmod SOURCES src/engine.cpp)
# The pattern asset is JSON: the vendored single header, compiled in (as drum).
target_link_libraries(stepmod PRIVATE arb_json)

set(_box "${ARB_BOX_ROOT}/arbutus/stepmod")
set(_pattern "${CMAKE_CURRENT_SOURCE_DIR}/tests/pattern.json")

# Goldens frozen from this box; README.md says what was checked. arbutus-run's
# transport is playing, so both are song-locked.
# Default ramp, 8 steps of 1/16 at 120 BPM, PING, SLEW 0.5.
add_test(NAME stepmod_golden_ramp COMMAND arbutus-run "${_box}"
         --len 96000 --sr 48000 --frames 128 --tempo 120 --set dir=2 --set slew=0.5
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/stepmod_ramp.npy" --tol -100)
# The pattern asset (10 values) over 12 steps of 1/8, RAND, RANGE 0.5, seed 3.
add_test(NAME stepmod_golden_pattern COMMAND arbutus-run "${_box}"
         --asset pattern=${_pattern} --len 96000 --sr 48000 --frames 512 --tempo 90 --seed 3
         --set steps=12 --set div=3 --set dir=3 --set range=0.5
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/stepmod_pattern.npy" --tol -100)

add_test(NAME rt_guard_stepmod COMMAND arbutus-run "${_box}" --rt-guard
         --asset pattern=${_pattern} --len 96000 --sr 48000 --set slew=0.3 --set dir=3)
