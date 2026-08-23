FROM golang:1.27 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /gateway ./cmd/gateway

FROM gcr.io/distroless/static-debian13
COPY --from=build /gateway /gateway
ENTRYPOINT ["/gateway"]