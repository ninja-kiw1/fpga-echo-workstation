/* hog.c -- load generator for J3(b) (stand-in for stress-ng, which is not on the board)
 *   hog cpu N SECONDS   N threads, integer ALU loop          (~ stress-ng --cpu N)
 *   hog mem N SECONDS   N threads, memcpy 2 x 32 MB per thread (~ stress-ng --stream N)
 * Thread i is pinned to core i % 4. Prints work done per thread at exit.
 * gcc -O2 -std=gnu99 -pthread hog.c -o hog
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int g_mem, g_secs;
static volatile int g_stop;

static double now_s(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void *hog(void *arg)
{
    long id = (long)arg;
    cpu_set_t s;
    uint64_t work = 0;

    CPU_ZERO(&s);
    CPU_SET((int)(id % 4), &s);
    pthread_setaffinity_np(pthread_self(), sizeof(s), &s);
    if (g_mem) {
        size_t n = 32u << 20;
        uint8_t *a = malloc(n), *b = malloc(n);
        memset(a, 1, n);
        memset(b, 2, n);
        while (!g_stop) {
            memcpy(b, a, n);
            memcpy(a, b, n);
            work += 2;
        }
        printf("hog %ld mem copies=%llu (%.0f MB)\n", id, (unsigned long long)work, work * 32.0);
    } else {
        volatile uint32_t x = (uint32_t)id + 1u;
        while (!g_stop) {
            int i;
            for (i = 0; i < 100000; i++) {
                x = x * 1664525u + 1013904223u;
            }
            work++;
        }
        printf("hog %ld cpu loops=%llu\n", id, (unsigned long long)work);
    }
    return NULL;
}

int main(int argc, char **argv)
{
    int n, i;
    pthread_t th[16];
    double t0;

    if (argc < 4) {
        fprintf(stderr, "usage: hog cpu|mem N SECONDS\n");
        return 1;
    }
    g_mem  = strcmp(argv[1], "mem") == 0;
    n      = atoi(argv[2]);
    g_secs = atoi(argv[3]);
    if (n < 1 || n > 16) return 1;
    for (i = 0; i < n; i++) pthread_create(&th[i], NULL, hog, (void *)(long)i);
    t0 = now_s();
    while (now_s() - t0 < g_secs) {
        struct timespec ts = { 0, 100000000 };
        nanosleep(&ts, NULL);
    }
    g_stop = 1;
    for (i = 0; i < n; i++) pthread_join(th[i], NULL);
    return 0;
}
