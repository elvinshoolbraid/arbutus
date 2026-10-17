# A modulator that reports its side[] and motion[] raw, so host tests can see
# exactly which block of which bus a sidechain source delivered.
add_arbutus_engine(sidemod SOURCES src/engine.cpp)
