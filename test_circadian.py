import math

def get_circadian_state(hour: float) -> dict:
    points = [
        (6.0,  3000, 50),
        (9.0,  5500, 100),
        (17.0, 5500, 100),
        (19.0, 3500, 70),
        (22.0, 2200, 20),
        (30.0, 3000, 50), # next 6 AM
    ]
    
    if hour < 6.0:
        hour += 24.0
        
    for i in range(len(points) - 1):
        t1, temp1, dim1 = points[i]
        t2, temp2, dim2 = points[i+1]
        
        if t1 <= hour < t2:
            progress = (hour - t1) / (t2 - t1)
            progress = -(math.cos(math.pi * progress) - 1) / 2
            t = int(temp1 + (temp2 - temp1) * progress)
            d = int(dim1 + (dim2 - dim1) * progress)
            return {"temp": t, "dimming": d}
            
    return {"temp": 2200, "dimming": 20}

def test_circadian():
    tests = [
        (6.0, 3000, 50),
        (9.0, 5500, 100),
        (13.0, 5500, 100),
        (17.0, 5500, 100),
        (19.0, 3500, 70),
        (22.0, 2200, 20),
        (3.0, 2600, 35), # Halfway between 10PM (2200K, 20) and 6AM (3000K, 50). Sine easing makes it linear at 50%.
    ]
    
    for h, expected_t, expected_d in tests:
        res = get_circadian_state(h)
        print(f"Time: {h:05.2f} -> {res['temp']}K, {res['dimming']}%")
        # Allow small math drift due to int casting and sine
        assert abs(res["temp"] - expected_t) <= 100, f"Expected {expected_t}, got {res['temp']} at {h}"
        assert abs(res["dimming"] - expected_d) <= 5, f"Expected {expected_d}, got {res['dimming']} at {h}"
        
    print("✅ All circadian math tests passed!")

if __name__ == "__main__":
    test_circadian()
