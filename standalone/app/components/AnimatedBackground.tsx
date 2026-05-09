"use client";

/**
 * Subtle moving-orb backdrop.
 *
 * Three blurred radial gradients (Mistral orange, deep navy, warm tint)
 * drift slowly across the viewport. Pure CSS, no JS animation loop, no
 * runtime cost. Sits at z-index -1 so all UI renders on top.
 */
export default function AnimatedBackground() {
  return (
    <div className="bg-mesh" aria-hidden>
      <div className="third-orb" />
    </div>
  );
}
