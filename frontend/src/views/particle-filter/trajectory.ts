import type { ParticleTrajectoryPoint } from "@/types";

export interface TrajectoryFrame {
  point: ParticleTrajectoryPoint;
  leftIndex: number;
  rightIndex: number;
  mix: number;
  timeMs: number;
}

function lerp(left: number, right: number, mix: number): number {
  return left + (right - left) * mix;
}

export function pointTime(
  point: ParticleTrajectoryPoint,
  fallback: number,
): number {
  const value = Date.parse(point.observed_at);
  return Number.isFinite(value) ? value : fallback;
}

export function trajectoryFrame(
  points: ParticleTrajectoryPoint[],
  rawProgress: number,
): TrajectoryFrame | null {
  if (!points.length) return null;
  const progress = Math.min(1, Math.max(0, rawProgress));
  if (points.length === 1) {
    return {
      point: points[0],
      leftIndex: 0,
      rightIndex: 0,
      mix: 0,
      timeMs: pointTime(points[0], 0),
    };
  }

  const times = points.map((point, index) => pointTime(point, index));
  const firstTime = times[0];
  const lastTime = times[times.length - 1];
  const useObservedTime = lastTime > firstTime;
  const targetTime = useObservedTime
    ? lerp(firstTime, lastTime, progress)
    : progress * (points.length - 1);
  const timeline = useObservedTime
    ? times
    : points.map((_point, index) => index);

  let rightIndex = timeline.findIndex((time) => time >= targetTime);
  if (rightIndex < 0) rightIndex = points.length - 1;
  if (rightIndex === 0) {
    return {
      point: points[0],
      leftIndex: 0,
      rightIndex: 0,
      mix: 0,
      timeMs: firstTime,
    };
  }

  const leftIndex = rightIndex - 1;
  const segmentDuration = Math.max(
    Number.EPSILON,
    timeline[rightIndex] - timeline[leftIndex],
  );
  const mix = Math.min(
    1,
    Math.max(0, (targetTime - timeline[leftIndex]) / segmentDuration),
  );
  if (mix >= 1) {
    return {
      point: points[rightIndex],
      leftIndex: rightIndex,
      rightIndex,
      mix: 0,
      timeMs: useObservedTime ? times[rightIndex] : lastTime,
    };
  }

  const left = points[leftIndex];
  const right = points[rightIndex];
  return {
    leftIndex,
    rightIndex,
    mix,
    timeMs: useObservedTime
      ? targetTime
      : lerp(times[leftIndex], times[rightIndex], mix),
    point: {
      ...left,
      observed_at: new Date(
        useObservedTime
          ? targetTime
          : lerp(times[leftIndex], times[rightIndex], mix),
      ).toISOString(),
      estimated_percent: lerp(
        left.estimated_percent,
        right.estimated_percent,
        mix,
      ),
      estimated_percent_lower: lerp(
        left.estimated_percent_lower,
        right.estimated_percent_lower,
        mix,
      ),
      estimated_percent_upper: lerp(
        left.estimated_percent_upper,
        right.estimated_percent_upper,
        mix,
      ),
      capacity_usd: lerp(left.capacity_usd, right.capacity_usd, mix),
      capacity_lower_usd: lerp(
        left.capacity_lower_usd,
        right.capacity_lower_usd,
        mix,
      ),
      capacity_upper_usd: lerp(
        left.capacity_upper_usd,
        right.capacity_upper_usd,
        mix,
      ),
      ess_fraction: lerp(left.ess_fraction, right.ess_fraction, mix),
      boundary_mass: {
        lower: lerp(left.boundary_mass.lower, right.boundary_mass.lower, mix),
        upper: lerp(left.boundary_mass.upper, right.boundary_mass.upper, mix),
      },
      particles_usd: left.particles_usd.map((value, index) =>
        lerp(value, right.particles_usd[index] ?? value, mix),
      ),
      resampled: false,
    },
  };
}
