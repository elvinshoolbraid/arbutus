add_arbutus_engine(wavetable SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/wavetable")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")
set(_table "${ARB_AUDIO_ROOT}/library/synth/wavetable/harmonics.wav")

# The table is an asset, so every test loads one: without it the box is silent
# by design (there is nothing to scan), which is what the host's UNKNOWN /
# MISSING ASSET refusals are for.
add_test(NAME wavetable_golden COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 48000 --sr 48000 --frames 512
         --asset wavetable=${_table} --set pos=55 --set warp=30 --set fold=25
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/wavetable.npy" --tol -100)

add_test(NAME ragged_wavetable
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged"
                 --notes "${_notes}" --len 48000 --sr 48000
                 --asset wavetable=${_table} --set pos=55 --set warp=30 --set fold=25)

# #647: every run above leaves SWEEP at 0, where the envelope term #56 put on
# the control grid multiplies by zero, so a return of #56's bug (the envelope
# read once per call) passed both. SWEEP 50 makes it audible to ragged.sh
# (that probe: -12.4 dBFS apart at 2048 frames).
add_test(NAME ragged_wavetable_sweep
         COMMAND "${ARB_AUDIO_ROOT}/tests/ragged.sh"
                 $<TARGET_FILE:arbutus-run> "${_box}" "${CMAKE_CURRENT_BINARY_DIR}/ragged_sweep"
                 --notes "${_notes}" --len 48000 --sr 48000
                 --asset wavetable=${_table} --set pos=55 --set warp=30 --set fold=25
                 --set sweep=50)

add_test(NAME rt_guard_wavetable COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --len 48000 --sr 48000 --asset wavetable=${_table})

find_package(Python3 COMPONENTS Interpreter REQUIRED)

# The stock tables are a build, not a drawing: the script is the source and
# running it again must produce the same bytes (repo CLAUDE.md § Library).
add_test(NAME wavetable_assets COMMAND ${Python3_EXECUTABLE}
         "${CMAKE_CURRENT_SOURCE_DIR}/tests/check_tables.py"
         "${ARB_AUDIO_ROOT}/tools/mkwavetable.py"
         "${ARB_AUDIO_ROOT}/library/synth/wavetable")
