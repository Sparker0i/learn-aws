package hello

import "testing"

func TestMess(t *testing.T) {
	tests := []struct {
		name string
		want string
	}{
		{name: "returns the gateway greeting", want: "gateway: hello"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := Message(); got != tt.want {
				t.Errorf("Message() = %q, want %q", got, tt.want)
			}
		})
	}
}
