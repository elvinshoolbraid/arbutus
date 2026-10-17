add_arbutus_engine(biquad SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/biquad")
set(_pygold "${ARB_AUDIO_ROOT}/tests/golden/python")

# The cross-language proof: the Python capture (48 kHz, cutoff 1200, Q 0.9 —
# emulator/tests/test_dsp_golden.py::test_biquad) replayed through the box.
add_test(NAME biquad_golden COMMAND arbutus-run "${_box}"
         --in "${_pygold}/biquad_input.npy"
         --set cutoff=1200 --set q=0.9
         --sr 48000 --frames 512
         --golden "${_pygold}/biquad.npy" --tol -100)
