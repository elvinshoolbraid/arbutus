add_arbutus_engine(op1r-verb SOURCES src/engine.cpp src/plugin.c)
op1r_box(op1r-verb)

set(_box "${ARB_BOX_ROOT}/op1r/op1r-verb")
set(_in "${ARB_AUDIO_ROOT}/tests/golden/fx_input.npy")

# Frozen from this box (op1r's integer DSP has no float original to replay):
# a long, bright, diffuse tail, mostly wet, two seconds so the tail rings past
# the input's half second. README § Tests.
add_test(NAME op1r_verb_golden COMMAND arbutus-run "${_box}"
         --in "${_in}" --len 96000 --sr 48000 --frames 512
         --set time=0.8 --set tone=0.7 --set diffusion=0.7 --set mix=0.6
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/op1r-verb.npy" --tol -100)

add_test(NAME ragged_op1r_verb
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --in "${_in}" --len 48000 --sr 48000
                 --set time=0.8 --set tone=0.7 --set diffusion=0.7 --set mix=0.6)

add_test(NAME rt_guard_op1r_verb COMMAND arbutus-run "${_box}" --rt-guard
         --in "${_in}" --len 48000 --sr 48000 --set time=1 --set mix=1)

# The adapter's own promises: dry is the input 64 frames late, three FX
# positions and no fourth, instances that share nothing, loud input bounded.
add_executable(test_op1r_verb tests/test_op1r_verb.cpp)
target_link_libraries(test_op1r_verb PRIVATE arb_sdk ${CMAKE_DL_LIBS})
add_dependencies(test_op1r_verb op1r-verb_box)
add_test(NAME op1r_verb_adapter COMMAND test_op1r_verb $<TARGET_FILE:op1r-verb>)
