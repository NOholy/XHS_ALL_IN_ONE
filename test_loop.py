import random
def test_loop(mu, sigma):
    total_sleep_time = max(2.0, random.normalvariate(mu, sigma))
    elapsed = 0.0
    swipes = 0
    iterations = 0
    while elapsed < total_sleep_time:
        chunk = random.uniform(2.0, 4.0)
        if elapsed + chunk > total_sleep_time:
            chunk = total_sleep_time - elapsed
        elapsed += chunk
        iterations += 1
        if elapsed < total_sleep_time and random.random() < 0.6:
            swipes += 1
    return swipes, iterations, total_sleep_time

swipes_total = 0
for i in range(100):
    s, iters, total = test_loop(5.0, 2.0)
    swipes_total += s
print(f"Average swipes over 100 runs: {swipes_total / 100}")
