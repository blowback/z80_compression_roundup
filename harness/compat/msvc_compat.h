/* Force-included when building RIP and OaPACK with g++ on Linux.
 * Supplies the MSVC-only pieces those sources rely on. */
#pragma once
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <exception>

#define __int64 long long

/* MSVC's std::exception accepts a message; libstdc++'s does not. */
class msvc_exception : public std::exception {
public:
    msvc_exception() : msg_("exception") {}
    explicit msvc_exception(const char *msg) : msg_(msg) {}
    const char *what() const noexcept override { return msg_; }
private:
    const char *msg_;
};
