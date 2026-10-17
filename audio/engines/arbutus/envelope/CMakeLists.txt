add_arbutus_engine(envelope SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/envelope")
set(_notes "${CMAKE_CURRENT_SOURCE_DIR}/tests/notes.txt")
set(_side "${ARB_AUDIO_ROOT}/tests/golden/fx_side.npy")

# Goldens frozen from this box; README.md says what was checked.
# NOTE: A 50 ms, D 200 ms, S 0.6, R 300 ms, CURVE +0.5.
add_test(NAME envelope_golden_note COMMAND arbutus-run "${_box}"
         --notes "${_notes}" --len 96000 --sr 48000 --frames 128
         --set attack=0.05 --set decay=0.2 --set sustain=0.6 --set release=0.3 --set curve=0.5
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/envelope_note.npy" --tol -100)
# GATE-IN on the duck's sidechain bursts, LOOP on while the gate is open.
add_test(NAME envelope_golden_gate COMMAND arbutus-run "${_box}"
         --side "${_side}" --len 48000 --sr 48000 --frames 128
         --set trig=1 --set loop=1 --set attack=0.01 --set decay=0.04 --set sustain=0.2
         --set release=0.1
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/envelope_gate.npy" --tol -100)
# FREE, LOOP on, CURVE -0.5, at 512-frame blocks.
add_test(NAME envelope_golden_free COMMAND arbutus-run "${_box}"
         --len 96000 --sr 48000 --frames 512
         --set trig=2 --set loop=1 --set attack=0.25 --set decay=0.5 --set sustain=0 --set curve=-0.5
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/envelope_free.npy" --tol -100)

add_test(NAME rt_guard_envelope COMMAND arbutus-run "${_box}" --rt-guard
         --notes "${_notes}" --side "${_side}" --len 96000 --sr 48000 --set loop=1)
