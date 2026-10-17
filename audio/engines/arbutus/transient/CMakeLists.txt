add_arbutus_engine(transient SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/transient")
# A drum bar is the input a transient shaper is for: arbutus/drum's own golden.
set(_in "${ARB_AUDIO_ROOT}/engines/arbutus/drum/tests/golden/drum.npy")

add_test(NAME transient_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512
         --set attack=60 --set sustain=-50 --set speed=20 --set output=-3
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/transient.npy" --tol -100)

# Default = a wire: the output is the input, compared as a golden.
add_test(NAME transient_transparent COMMAND arbutus-run "${_box}"
         --in "${_in}" --sr 48000 --frames 512 --golden "${_in}" --tol -100)

add_test(NAME ragged_transient
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --sr 48000 --set attack=60 --set sustain=-50 --set mix=70)

add_test(NAME rt_guard_transient COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --sr 48000 --set attack=60 --set sustain=-50)
