def fibonacci_recursive(n):
    """Calculates the nth Fibonacci number recursively."""
    if n <= 1:
        return n
    return fibonacci_recursive(n-1) + fibonacci_recursive(n-2)

def fibonacci_iterative(n):
    """Calculates the nth Fibonacci number iteratively."""
    a, b = 0, 1
    for _ in range(n): # We need n number so we iterate n times
        yield a
        a, b = b, a + b

def main():
    N = 10
    print(f"--- Fibonacci Sequence (First {N} numbers) ---\n")

    # 1. Recursive Approach (Calculating the Nth number, then printing sequence)
    print("--- Recursive Approach (Sequence up to N-1) ---")
    # Note: To print the sequence, we calculate each number individually
    sequence_recursive = [fibonacci_recursive(i) for i in range(N)]
    print(sequence_recursive)

    print("\n------------------------------------------\n")

    # 2. Iterative Approach
    print("--- Iterative Approach ---")
    sequence_iterative = list(fibonacci_iterative(N))
    print(sequence_iterative)

if __name__ == "__main__":
    main()