def fibonacci_recursive(n):
    """Calculates the n-th Fibonacci number recursively."""
    if n <= 1:
        return n
    return fibonacci_recursive(n - 1) + fibonacci_recursive(n - 2)

def fibonacci_iterative(n):
    """Calculates the n-th Fibonacci number iteratively."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

def main():
    """Prints the first 10 Fibonacci numbers using both methods."""
    N = 10
    print(f"\n--- First {N} Fibonacci Numbers (Recursive) ---")
    for i in range(N):
        print(fibonacci_recursive(i))

    print(f"\n--- First {N} Fibonacci Numbers (Iterative) ---")
    # The iterative approach requires tracking the sequence manually for printing the first N.
    current = 0
    for i in range(N):
        print(current)
        # Python swap for (current, current + 1) sequence
        current, current + 1 = current + 1, current + 2

if __name__ == "__main__":
    main()