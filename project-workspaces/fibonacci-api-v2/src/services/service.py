# 1. Input Validation and Error Handling
  if not isinstance(n, int) or n < 0:
    raise ValueError("Input 'n' must be a non-negative integer.")

  # 2. Caching Layer Implementation (In-Memory Example)
  # Check cache first
  if hasattr(Service, 'cache') and n in Service.cache:
    print(f"[Cache Hit] Returning cached result for n={n}.")
    return Service.cache[n]

  print(f"[Cache Miss] Processing request for n={n}...")
  # Simulate expensive computation
  result = n * n * 2

  # Store result in cache
  if not hasattr(Service, 'cache'):
    Service.cache = {}
  Service.cache[n] = result
  print(f"[Cache] Stored result for n={n} in cache.")
  return result