add_arbutus_engine(follower SOURCES src/engine.cpp)

set(_box "${ARB_BOX_ROOT}/arbutus/follower")
set(_side "${ARB_AUDIO_ROOT}/tests/golden/fx_side.npy")

# A modulator's golden is its control outputs, one channel per output, each
# block's value held (arbutus-run). Frozen from this box; README.md says what
# was checked. fx_side.npy is the duck's sidechain: bursts and silence.
add_test(NAME follower_golden COMMAND arbutus-run "${_box}"
         --side "${_side}" --len 36000 --sr 48000 --frames 128
         --set attack=2 --set release=80 --set gain=3 --set thresh=-12 --set hold=40
         --golden "${CMAKE_CURRENT_SOURCE_DIR}/tests/golden/follower.npy" --tol -100)

add_test(NAME rt_guard_follower COMMAND arbutus-run "${_box}" --rt-guard
         --side "${_side}" --sr 48000)
