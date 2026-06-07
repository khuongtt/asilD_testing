package com.example;

import java.util.List;

/**
 * @SafetyCritical
 * @AsilD
 */
public class BrakeController {
    
    @SafetyCritical
    @AsilD
    public double calculateBrakePressure(double speed, double distance) {
        if (speed > 0 && distance > 0) {
            return speed / distance;
        }
        return 0;
    }
}
