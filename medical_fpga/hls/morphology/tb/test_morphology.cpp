#include "morphology.hpp"
#include <fstream>
#include <iostream>
#include <vector>
#include <string>
#include <algorithm>

static bool read_bytes(const std::string& path, std::vector<unsigned char>& bytes, int count) {
    std::ifstream f(path.c_str(), std::ios::binary);
    if (!f) return false;
    bytes.resize(count);
    if (count) {
        f.read(reinterpret_cast<char*>(&bytes[0]), count);
        if (f.gcount() != count) return false;
    }
    return f.peek() == std::ifstream::traits_type::eof();
}
int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "Expected vector directory\n"; return 2; }
    std::string base = argv[1];
    std::ifstream cases((base + "/cases.txt").c_str());
    if (!cases) return 2;
    std::vector<ap_uint<8> > input(MAX_PIXELS), output(MAX_PIXELS);
    std::string name; int width, height, mode, count, tested = 0;
    unsigned long total_errors = 0;
    while (cases >> name >> width >> height >> mode >> count) {
        bool valid = width >= 1 && width <= MAX_WIDTH && height >= 1 && height <= MAX_HEIGHT && mode >= 0 && mode <= 3;
        if (mode < 0 || count != (valid ? width * height : 0)) return 2;
        std::vector<unsigned char> source, expected;
        if (!read_bytes(base + "/" + name + "_input.bin", source, count) ||
            !read_bytes(base + "/" + name + "_expected.bin", expected, count)) return 2;
        std::fill(output.begin(), output.end(), ap_uint<8>(173));
        for (int i = 0; i < count; ++i) input[i] = source[i];
        morphology_binary(&input[0], &output[0], width, height, mode);
        unsigned long errors = 0;
        std::ofstream actual((base + "/" + name + "_actual.bin").c_str(), std::ios::binary);
        for (int i = 0; i < count; ++i) {
            unsigned char value = static_cast<unsigned char>(output[i].to_uint());
            actual.write(reinterpret_cast<const char*>(&value), 1);
            if (value != expected[i]) {
                if (errors < 3) std::cerr << name << " index=" << i << " expected="
                    << int(expected[i]) << " actual=" << int(value) << "\n";
                ++errors;
            }
        }
        // Invalid dimensions must leave the entire output untouched.
        for (int i = count; i < MAX_PIXELS; ++i) if (output[i] != 173) {
            ++errors; std::cerr << "Unexpected write: " << name << "\n"; break;
        }
        if (!actual) return 2;
        std::cout << (errors ? "FAIL " : "PASS ") << name << " mode=" << mode
                  << " pixels=" << count << " mismatches=" << errors << "\n";
        total_errors += errors; ++tested;
    }
    if (!cases.eof()) return 2;
    std::cout << "SUMMARY cases=" << tested << " mismatches=" << total_errors << "\n";
    return tested && !total_errors ? 0 : 1;
}
