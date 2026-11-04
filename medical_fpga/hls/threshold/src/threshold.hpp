#ifndef THRESHOLD_HPP
#define THRESHOLD_HPP
#include <ap_int.h>
static const int MAX_WIDTH = 700;
static const int MAX_HEIGHT = 605;
static const int MAX_PIXELS = MAX_WIDTH * MAX_HEIGHT;
void threshold_binary(const ap_uint<8>* input, ap_uint<8>* output,
                      int width, int height, ap_uint<8> threshold_value);
#endif
