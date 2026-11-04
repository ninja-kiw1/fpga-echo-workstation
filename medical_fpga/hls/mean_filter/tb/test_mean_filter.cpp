#include "mean_filter.hpp"
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <algorithm>

static bool read_bytes(const std::string& path, std::vector<unsigned char>& bytes, int count) {
    std::ifstream f(path.c_str(), std::ios::binary);
    if (!f) return false;
    bytes.resize(count);
    f.read(reinterpret_cast<char*>(&bytes[0]), count);
    return f.gcount() == count && f.peek() == std::ifstream::traits_type::eof();
}
int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "Expected vector directory argument\n"; return 2; }
    std::string base = argv[1];
    std::ifstream cases((base + "/cases.txt").c_str());
    if (!cases) { std::cerr << "Cannot open cases.txt\n"; return 2; }
    // Full interface depth is allocated for the RTL co-simulation adapter.
    std::vector<ap_uint<8> > input(MAX_PIXELS), output(MAX_PIXELS);
    std::string name; int width, height, tested = 0;
    unsigned long total_errors = 0;
    while (cases >> name >> width >> height) {
        if (width < 1 || width > MAX_WIDTH || height < 1 || height > MAX_HEIGHT) return 2;
        int count = width * height;
        std::vector<unsigned char> source, expected;
        if (!read_bytes(base + "/" + name + "_input.bin", source, count) ||
            !read_bytes(base + "/" + name + "_expected.bin", expected, count)) {
            std::cerr << "Bad vector: " << name << "\n"; return 2;
        }
        std::fill(output.begin(), output.end(), ap_uint<8>(173));
        for (int i = 0; i < count; ++i) input[i] = source[i];
        mean_filter(&input[0], &output[0], width, height);
        unsigned long errors = 0; int max_error = 0;
        std::ofstream actual((base + "/" + name + "_actual.bin").c_str(), std::ios::binary);
        for (int i = 0; i < count; ++i) {
            unsigned char value = static_cast<unsigned char>(output[i].to_uint());
            actual.write(reinterpret_cast<const char*>(&value), 1);
            int delta = int(value) - int(expected[i]);
            if (delta < 0) delta = -delta;
            if (delta) {
                if (errors < 3) std::cerr << name << " (" << i / width << "," << i % width
                    << ") expected=" << int(expected[i]) << " actual=" << int(value) << "\n";
                ++errors; max_error = std::max(max_error, delta);
            }
        }
        // Detect writes beyond the valid output frame.
        for (int i = count; i < MAX_PIXELS; ++i) if (output[i] != 173) {
            ++errors; std::cerr << "Output overrun: " << name << "\n"; break;
        }
        if (!actual) { std::cerr << "Output write failed\n"; return 2; }
        std::cout << (errors ? "FAIL " : "PASS ") << name << " " << width << "x" << height
                  << " mismatches=" << errors << " max_error=" << max_error << "\n";
        total_errors += errors; ++tested;
    }
    std::cout << "SUMMARY cases=" << tested << " mismatches=" << total_errors << "\n";
    return tested > 0 && total_errors == 0 ? 0 : 1;
}
