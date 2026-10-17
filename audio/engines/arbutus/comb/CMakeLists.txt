add_arbutus_engine(comb SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/comb")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# No Python original for this one: the golden was frozen from this box after
# checking peak / RMS / DC / finiteness (see README.md).
add_test(NAME comb_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --len 32768 --sr 48000 --frames 512
         --set tune=-5 --set feedback=0.9 --set damp=0.3 --set mix=0.7
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/comb.npy" --tol -100)

# Block size must not be audible: a comb's state is a ring, not a block.
add_test(NAME ragged_comb
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --sr 48000 --set tune=-5 --set feedback=0.9)

add_test(NAME rt_guard_comb COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --sr 48000)
