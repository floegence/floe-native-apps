package main

import "testing"

func TestQualificationRecipesNeverFallback(t *testing.T) {
	for _, architecture := range []string{"amd64", "arm64"} {
		xpra, err := packageForRecipe("xpra", architecture)
		if err != nil {
			t.Fatal(err)
		}
		desktop, err := packageForRecipe("desktop", architecture)
		if err != nil {
			t.Fatal(err)
		}
		if xpra.Preparation != nil || desktop.Preparation == nil || xpra.Digest() == desktop.Digest() {
			t.Fatal("qualification recipe selection was lost")
		}
	}
	if _, err := packageForRecipe("unknown", "amd64"); err == nil {
		t.Fatal("unknown recipe fell back")
	}
}
