#ifndef MORPHOLOGY_HPP
#define MORPHOLOGY_HPP
#include <ap_int.h>
static const int MAX_WIDTH = 700;
static const int MAX_HEIGHT = 605;
static const int MAX_PIXELS = MAX_WIDTH * MAX_HEIGHT;
enum MorphMode { ERODE = 0, DILATE = 1, OPEN = 2, CLOSE = 3 };
void morphology_binary(const ap_uint<8>* input, ap_uint<8>* output,
                       int width, int height, unsigned int mode);
#endif
