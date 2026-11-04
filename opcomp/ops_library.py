"""Operators written in the opcomp IR. Each returns the output node (inputs R, G, B in [0, 1]).

Echo (grey frames t-1, t, t+1 in R, G, B), fundus (colour, vessels darkest in green), any modality.
All are exact compositions of linear 3x3 filters and ReLUs, so the compiled engine reproduces them
up to integer rounding.
"""
import numpy as np

from ir import B, G, R, abs_, clip, conv, max_, mean, median3, relu

# int8-friendly kernels: integer taps whose largest is 127, divided by their sum (or by the sum of the
# positive taps for the gradient). The quantizer maps the largest weight of a channel to 127, so these
# taps quantize exactly and the DC gain stays exactly 1 (the textbook [1 2 1] / [1 2 4] ratios cannot be
# represented with a largest tap of 127, and the 0.6 % gain error per pass adds up over cascades).
BINOM = np.array([[32, 64, 32], [64, 127, 64], [32, 64, 32]], float) / 511.0      # ~ binomial 3x3
SOBEL_X = np.array([[-64, 0, 64], [-127, 0, 127], [-64, 0, 64]], float) / 255.0   # ~ Sobel / 4
SOBEL_Y = SOBEL_X.T
# 3-tap line averages in 4 directions (0, 45, 90, 135 degrees)
LINE = {
    0: np.array([[0, 0, 0], [1, 1, 1], [0, 0, 0]], float) / 3.0,
    45: np.array([[0, 0, 1], [0, 1, 0], [1, 0, 0]], float) / 3.0,
    90: np.array([[0, 1, 0], [0, 1, 0], [0, 1, 0]], float) / 3.0,
    135: np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], float) / 3.0,
}


def blur(x, times):
    for _ in range(times):
        x = conv(x, BINOM)
    return x


def op_absdiff():
    """|R - G|: frame difference (the F5 registration cost)."""
    return abs_(R - G)


def op_median3():
    """temporal median of three frames (speckle / impulse noise reduction)."""
    return median3(R, G, B)


def op_mean3():
    return mean(R, G, B)


def op_sobel():
    """L1 gradient magnitude of R: |Gx| + |Gy| (Sobel / 4)."""
    return abs_(conv(R, SOBEL_X)) + abs_(conv(R, SOBEL_Y))


def op_gauss11():
    """11 x 11 binomial blur of R (5 cascaded [1 2 1] passes, sigma ~ 1.58 px)."""
    return blur(R, 5)


def op_unsharp():
    """contrast enhancement: clip(R + 1.5 (R - blur3(R)), 0, 1)."""
    return clip(R + 1.5 * (R - blur(R, 2)), 0.0, 1.0)


def op_motion():
    """motion energy |t - (t-1)| + |(t+1) - t| for three consecutive frames in R, G, B."""
    return abs_(G - R) + abs_(B - G)


def op_change():
    """thresholded change: relu(|R - G| - 24/255), i.e. differences above 24 grey levels."""
    return relu(abs_(R - G) - 24.0 / 255.0)


def op_vessel4():
    """4-direction line detector on the green channel (vessels are dark): max over directions of
    (7-tap line mean - 7 x 7 local mean) of the inverted green. Ricci & Perfetti 2007 style, short scale."""
    inv = 1.0 - G
    lines = {}
    for a, k in LINE.items():
        x = inv
        for _ in range(3):                    # 3 cascaded 3-tap line means = 7-tap (triangular) line mean
            x = conv(x, k)
        lines[a] = x
    local = blur(inv, 3)                      # 7 x 7 binomial local mean
    d = {a: lines[a] - local for a in lines}
    return max_(max_(d[0], d[90]), max_(d[45], d[135]))


# name -> (builder, output range). The range must be a true mathematical bound of the operator (not
# the interval estimate, which ignores correlations: median3 is in [0, 1] but intervals give [-2, 3]);
# the board's int8 output then spans exactly this range (1 LSB = range / 254).
OPS = dict(absdiff=(op_absdiff, (0.0, 1.0)), median3=(op_median3, (0.0, 1.0)), mean3=(op_mean3, (0.0, 1.0)),
           sobel=(op_sobel, (0.0, 2.0)), gauss11=(op_gauss11, (0.0, 1.0)), unsharp=(op_unsharp, (0.0, 1.0)),
           motion=(op_motion, (0.0, 2.0)), change=(op_change, (0.0, 1.0)), vessel4=(op_vessel4, (-1.0, 1.0)))
